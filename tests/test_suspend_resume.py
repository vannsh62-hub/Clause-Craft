"""Suspend and resume — the riskiest mechanism in the system.

The first four tests pin facts about the SDK that were established by experiment, not by
reading docs. Each is load-bearing, and each would fail silently if a version bump changed it:

1. `SuspendRun` reaches us wrapped in `UserError`, not as itself.
2. Completed turns are persisted to the session; the suspending turn is not.
3. Therefore there is **no orphaned tool call**, and nothing to repair on resume.
4. Resume is a plain user message. A `function_call_output` here would be an output with no
   matching call — the exact breakage it was supposed to avoid.

The rest cover the property that actually protects the budget: counters live in Postgres, so
answering a question does not buy a fresh set of drafting attempts.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from agents import Agent, RunConfig, Runner
from agents.exceptions import UserError
from agents.extensions.memory import SQLAlchemySession
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.core.config import settings
from backend.core.run_context import RunContext
from backend.schemas.errors import ControlSignal, CostCeilingExceeded, SuspendRun
from backend.subagents.orchestrator import deep_agent
from backend.tools.user_tool import ask_user, unwrap_control_signal
from backend.workspace.ledger import rehydrate_counters
from backend.workspace.models import Contract, ContractVersion, PendingQuestion
from tests.fakes import FakeModel, Turn, text_message, tool_call
from tests.helpers import render_clauses_for

ASK = {"questions": [{"name": "effective_date", "question": "When?", "type": "date"}]}


@pytest_asyncio.fixture
async def db() -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker[AsyncSession], uuid.UUID]]:
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    cid = uuid.uuid4()
    async with factory() as s:
        s.add(Contract(id=cid, contract_type="nda", request="Draft an NDA"))
        await s.commit()
    try:
        yield engine, factory, cid
    finally:
        async with factory() as s:
            await s.execute(delete(Contract).where(Contract.id == cid))
            await s.commit()
        await SQLAlchemySession(str(cid), engine=engine, create_tables=True).clear_session()
        await engine.dispose()


# ============================================================ the SDK contract, pinned


async def test_suspend_run_arrives_wrapped_in_user_error_not_as_itself(
    db: tuple[AsyncEngine, async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    """`failure_error_function=None` re-raises out of the *tool*. The runner then wraps it:
    `raise UserError(f"Error running tool ...") from e`. Catching SuspendRun directly around
    `Runner.run` would never fire."""
    engine, factory, cid = db
    ctx = RunContext(contract_id=cid, session_factory=factory)
    fake = FakeModel([Turn(output=[tool_call("ask_user", ASK, call_id="call_ABC")])])
    agent = Agent[RunContext](name="o", instructions="go", model=fake, tools=[ask_user])

    with pytest.raises(UserError) as exc:
        await Runner.run(
            agent,
            input="draft",
            context=ctx,
            max_turns=4,
            session=None,
            run_config=RunConfig(tracing_disabled=True),
        )

    assert not isinstance(exc.value, SuspendRun)

    signal = unwrap_control_signal(exc.value)
    assert isinstance(signal, SuspendRun)
    assert signal.call_id == "call_ABC"
    assert signal.questions[0]["name"] == "effective_date"


async def test_a_completed_turn_survives_the_suspension_but_the_suspending_turn_does_not(
    db: tuple[AsyncEngine, async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    """The plan the agent wrote survives. The `ask_user` call itself never reaches the session,
    because the turn aborts before its model response is persisted."""
    engine, factory, cid = db
    ctx = RunContext(contract_id=cid, session_factory=factory)
    session = SQLAlchemySession(str(cid), engine=engine, create_tables=True)

    fake = FakeModel(
        [
            Turn(output=[tool_call("ls_files", {}, call_id="c1")]),  # completes
            Turn(output=[tool_call("ask_user", ASK, call_id="c2")]),  # suspends
        ]
    )
    from backend.tools.workspace_tools import ls_files

    agent = Agent[RunContext](name="o", instructions="go", model=fake, tools=[ls_files, ask_user])

    with pytest.raises(UserError):
        await Runner.run(
            agent,
            input="draft",
            context=ctx,
            max_turns=6,
            session=session,
            run_config=RunConfig(tracing_disabled=True),
        )

    items = await session.get_items()
    calls = [i for i in items if i.get("type") == "function_call"]
    outputs = [i for i in items if i.get("type") == "function_call_output"]

    assert [c["call_id"] for c in calls] == ["c1"], "the completed turn must survive"
    assert [o["call_id"] for o in outputs] == ["c1"]
    assert "c2" not in str(items), "the suspending tool call must not be persisted"


async def test_there_is_no_orphaned_tool_call_to_repair(
    db: tuple[AsyncEngine, async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    """Every persisted `function_call` has a matching `function_call_output`.

    The research this was planned from expected an orphan here, and a resume that injected a
    paired `function_call_output`. Doing that would create an output with no matching call.
    """
    engine, factory, cid = db
    ctx = RunContext(contract_id=cid, session_factory=factory)
    session = SQLAlchemySession(str(cid), engine=engine, create_tables=True)

    fake = FakeModel([Turn(output=[tool_call("ask_user", ASK, call_id="call_ORPHAN")])])
    agent = Agent[RunContext](name="o", instructions="go", model=fake, tools=[ask_user])

    with pytest.raises(UserError):
        await Runner.run(
            agent,
            input="draft",
            context=ctx,
            max_turns=4,
            session=session,
            run_config=RunConfig(tracing_disabled=True),
        )

    items = await session.get_items()
    call_ids = {i["call_id"] for i in items if i.get("type") == "function_call"}
    output_ids = {i["call_id"] for i in items if i.get("type") == "function_call_output"}

    assert call_ids == output_ids == set(), "nothing from the suspending turn should persist"
    assert not (call_ids - output_ids), "an orphaned call would break the next completion"


async def test_resume_is_a_plain_user_message(
    db: tuple[AsyncEngine, async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    engine, factory, cid = db
    fake = FakeModel(
        [
            Turn(output=[tool_call("ask_user", ASK, call_id="c1")]),
            Turn(output=[text_message("Drafted, thanks.")]),
        ]
    )

    first = await deep_agent.start_run(cid, "Draft an NDA", engine, factory, model=fake)
    assert first.status == "awaiting_input"
    assert first.call_id == "c1"

    second = await deep_agent.resume_run(
        cid, {"effective_date": "2026-08-01"}, engine, factory, model=fake
    )
    assert second.status == "complete"
    assert "Drafted" in second.message

    resumed_input = fake.captures[-1].as_text()
    assert "2026-08-01" in resumed_input
    assert "function_call_output" not in resumed_input


# ============================================================ ask_user persistence


async def test_ask_user_persists_the_questions_and_the_call_id(
    db: tuple[AsyncEngine, async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    engine, factory, cid = db
    fake = FakeModel([Turn(output=[tool_call("ask_user", ASK, call_id="call_XYZ")])])

    outcome = await deep_agent.start_run(cid, "Draft an NDA", engine, factory, model=fake)

    assert outcome.status == "awaiting_input"
    assert outcome.questions and outcome.questions[0]["question"] == "When?"

    async with factory() as s:
        row = (
            await s.execute(select(PendingQuestion).where(PendingQuestion.contract_id == cid))
        ).scalar_one()
        contract = await s.get(Contract, cid)

    assert row.call_id == "call_XYZ"
    assert row.answered_at is None
    assert contract is not None and contract.status == "awaiting_input"


async def test_resuming_a_contract_that_is_not_waiting_is_refused(
    db: tuple[AsyncEngine, async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    """Idempotency: a client that retries POST /answers must not start a second run."""
    engine, factory, cid = db
    fake = FakeModel(
        [
            Turn(output=[tool_call("ask_user", ASK, call_id="c1")]),
            Turn(output=[text_message("done")]),
        ]
    )
    await deep_agent.start_run(cid, "Draft an NDA", engine, factory, model=fake)
    await deep_agent.resume_run(cid, {"effective_date": "2026-08-01"}, engine, factory, model=fake)

    with pytest.raises(ValueError, match="not waiting for an answer"):
        await deep_agent.resume_run(
            cid, {"effective_date": "2026-08-01"}, engine, factory, model=fake
        )


# ============================================================ the budget survives a resume


async def test_a_resume_does_not_reset_the_attempt_counter(
    db: tuple[AsyncEngine, async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    """Each slice builds a fresh RunContext. If the counter lived only in memory, a user could
    buy unlimited drafting attempts simply by being asked a question."""
    engine, factory, cid = db
    async with factory() as s:
        for attempt in (1, 2):
            s.add(
                ContractVersion(
                    contract_id=cid,
                    attempt=attempt,
                    path=f"draft_v{attempt}.md",
                    markdown="x",
                    input_tokens=1000,
                    output_tokens=200,
                )
            )
        await s.commit()

    ctx = RunContext(contract_id=cid, session_factory=factory)
    assert ctx.draft_attempts == 0 and ctx.total_tokens == 0  # a fresh slice knows nothing

    await rehydrate_counters(ctx)

    assert ctx.draft_attempts == 2, "attempts must be rebuilt from the ledger"
    assert ctx.input_tokens == 2000
    assert ctx.output_tokens == 400
    assert ctx.total_tokens == 2400


async def test_render_clauses_commits_the_contract_type_so_a_later_slice_can_validate(
    db: tuple[AsyncEngine, async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    """Found by a live run, invisible to the fakes.

    `render_clauses` took `contract_type` as an argument and threw it away. Every later slice
    built a fresh `RunContext` with `contract_type=None`, so `validate_draft` and the judge
    both failed with "contract type is not decided yet" — which the orchestrator, doing its
    best, reported to the user as "an internal error".
    """
    _, factory, cid = db
    async with factory() as s:
        contract = await s.get(Contract, cid)
        contract.contract_type = None  # type: ignore[union-attr]
        await s.commit()

    ctx = RunContext(contract_id=cid, session_factory=factory)
    assert ctx.contract_type is None

    await render_clauses_for(ctx)
    assert ctx.contract_type == "nda", "rendering commits to a clause set"

    async with factory() as s:
        assert (await s.get(Contract, cid)).contract_type == "nda"  # type: ignore[union-attr]

    fresh = RunContext(contract_id=cid, session_factory=factory)
    await rehydrate_counters(fresh)
    assert fresh.contract_type == "nda", "a resumed slice must know what to validate against"
    assert fresh.jurisdiction == "IN"


async def test_rehydration_clears_the_loop_detector_because_a_resume_is_new_intent(
    db: tuple[AsyncEngine, async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    _, factory, cid = db
    ctx = RunContext(contract_id=cid, session_factory=factory)
    ctx.tool_call_log["some-fingerprint"] = 2

    await rehydrate_counters(ctx)

    assert ctx.tool_call_log == {}


# ============================================================ control-signal plumbing


def test_unwrap_finds_a_signal_at_any_depth() -> None:
    signal = CostCeilingExceeded(300.0, 250.0)
    shallow = UserError("wrapped")
    shallow.__cause__ = signal
    deep = RuntimeError("outer")
    deep.__cause__ = shallow

    assert unwrap_control_signal(deep) is signal
    assert unwrap_control_signal(RuntimeError("nothing here")) is None


def test_control_signals_are_distinguishable_from_ordinary_failures() -> None:
    assert isinstance(SuspendRun("c", []), ControlSignal)
    assert isinstance(CostCeilingExceeded(1.0, 0.5), ControlSignal)
    assert unwrap_control_signal(UserError("plain")) is None
