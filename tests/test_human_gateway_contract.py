"""`ask_user` must be correct under a gateway that returns *and* one that does not.

This is the cheapest insurance available against the most expensive kind of migration bug.

Today's gateway never returns: it persists the questions, commits, and raises a control
signal that ends the run slice. A runtime with durable pause/resume would supply a gateway
that *does* return, with the answers. Code written against the raising gateway can quietly
assume the stack unwinds — and anything placed after the `ask` call is then dead code that
nobody notices, because under today's gateway it never ran and under tomorrow's it would.

The failure mode is nasty precisely because it is silent: work after `ask` would begin
executing on the day the runtime changed, having never been exercised. So the calling code
is run against both gateways here, while the raising one is still the only real
implementation.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from agents.tool_context import ToolContext
from agents.usage import Usage
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.core.config import settings
from backend.core.run_context import RunContext
from backend.runtime.human import SuspendingGateway
from backend.schemas.errors import SuspendRun
from backend.schemas.question import Question
from backend.tools import user_tool
from backend.workspace.models import Contract, PendingQuestion

QUESTIONS = [Question(name="effective_date", question="When does it start?", type="date")]


class RecordingGateway:
    """A gateway that returns, as a durable-pause runtime's would.

    It records the call so the test can assert the tool passed the right things, and
    returns answers so the tool's post-`ask` behaviour is exercised at all.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[uuid.UUID, list[Question], str]] = []

    async def ask(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        contract_id: uuid.UUID,
        questions: list[Question],
        call_id: str,
    ) -> dict[str, str]:
        self.calls.append((contract_id, questions, call_id))
        return {q.name: "2026-08-01" for q in questions}


@pytest_asyncio.fixture
async def ctx() -> AsyncIterator[RunContext]:
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    cid = uuid.uuid4()
    async with factory() as s:
        s.add(Contract(id=cid, contract_type="nda", request="Draft an NDA"))
        await s.commit()
    try:
        yield RunContext(contract_id=cid, session_factory=factory, contract_type="nda")
    finally:
        async with factory() as s:
            await s.execute(delete(Contract).where(Contract.id == cid))
            await s.commit()
        await engine.dispose()


def _tool_ctx(ctx: RunContext) -> ToolContext[RunContext]:
    return ToolContext(
        context=ctx, usage=Usage(), tool_name="ask_user", tool_call_id="call-1", tool_arguments="{}"
    )


async def _invoke(ctx: RunContext) -> str:
    payload = (
        '{"questions":[{"name":"effective_date","question":"When does it start?","type":"date"}]}'
    )
    return str(await user_tool.ask_user.on_invoke_tool(_tool_ctx(ctx), payload))


# ------------------------------------------------------------- the suspending gateway


async def test_the_suspending_gateway_never_returns(ctx: RunContext) -> None:
    with pytest.raises(SuspendRun) as caught:
        await _invoke(ctx)

    assert caught.value.call_id == "call-1"
    assert caught.value.questions[0]["name"] == "effective_date"


async def test_the_questions_are_committed_before_the_stack_unwinds(ctx: RunContext) -> None:
    """The commit is load-bearing.

    The control signal unwinds the stack. An uncommitted `PendingQuestion` would be rolled
    back with it, leaving a contract marked `awaiting_input` with nothing to answer — a
    run that can never be resumed.
    """
    with pytest.raises(SuspendRun):
        await _invoke(ctx)

    async with ctx.session_factory() as s:
        rows = (
            (
                await s.execute(
                    select(PendingQuestion).where(PendingQuestion.contract_id == ctx.contract_id)
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].call_id == "call-1"


# --------------------------------------------------------------- the returning gateway


async def test_the_same_tool_is_correct_under_a_gateway_that_returns(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Swap the gateway, change nothing else, and the tool still behaves."""
    gateway = RecordingGateway()
    monkeypatch.setattr(user_tool, "GATEWAY", gateway)

    result = await _invoke(ctx)

    assert gateway.calls, "the tool must delegate to the gateway, not suspend by itself"
    contract_id, questions, call_id = gateway.calls[0]
    assert contract_id == ctx.contract_id
    assert [q.name for q in questions] == ["effective_date"]
    assert call_id == "call-1"
    assert "effective_date: 2026-08-01" in result


async def test_the_tool_asks_once_not_once_per_question(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One call, every question. A tool that asked serially would suspend on the first and
    never reach the rest — under today's gateway the second call is unreachable code."""
    gateway = RecordingGateway()
    monkeypatch.setattr(user_tool, "GATEWAY", gateway)
    payload = (
        '{"questions":[{"name":"effective_date","question":"When?","type":"date"},'
        '{"name":"term_years","question":"How long?","type":"text"}]}'
    )

    await user_tool.ask_user.on_invoke_tool(_tool_ctx(ctx), payload)

    assert len(gateway.calls) == 1
    assert len(gateway.calls[0][1]) == 2


async def test_asking_nothing_is_refused_before_the_gateway_is_reached(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = RecordingGateway()
    monkeypatch.setattr(user_tool, "GATEWAY", gateway)

    with pytest.raises(ValueError, match="at least one question"):
        await user_tool.ask_user.on_invoke_tool(_tool_ctx(ctx), '{"questions":[]}')

    assert gateway.calls == []


def test_the_shipped_gateway_is_the_suspending_one() -> None:
    """A returning gateway in production would mean a process blocking on a human."""
    assert isinstance(user_tool.GATEWAY, SuspendingGateway)
