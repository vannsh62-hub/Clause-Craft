"""The deep agent: an orchestrator that plans, delegates, and decides.

A run happens in **slices**. A slice ends when the agent finishes, asks the user something,
exhausts its turns, or runs out of budget. `ask_user` cannot pause a coroutine across an HTTP
boundary — no process may block on a human — so it raises, the slice ends, and `resume()`
starts a new one. The workspace and the ledger carry everything across.

Three things established by experiment against this SDK, each pinned by a regression test in
`tests/test_suspend_resume.py`:

- **`SuspendRun` reaches us wrapped.** `failure_error_function=None` re-raises out of the tool,
  and the runner wraps it in `UserError(...) from e`. Unwrap `__cause__`.
- **Completed turns are persisted; the suspending turn is not.** The plan the agent wrote and
  the clauses it rendered survive a suspension. The `ask_user` tool call itself never reaches
  the session, so there is **no orphaned tool call** and nothing to repair.
- **Resume is a plain user message.** Injecting a `function_call_output` would create the very
  breakage it was meant to avoid: an output with no matching call.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from agents import Agent, RunConfig, Runner
from agents.exceptions import MaxTurnsExceeded
from agents.extensions.memory import SQLAlchemySession
from agents.models.interface import Model
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from backend.api.events import EventPublisher
from backend.core.config import settings
from backend.core.logging import get_logger
from backend.core.prompts import load_prompt
from backend.core.run_context import RunContext
from backend.runtime.adapters.openai_agents import OpenAIAgentsRuntime
from backend.runtime.spec import AgentSpec
from backend.schemas.errors import CostCeilingExceeded, SuspendRun
from backend.subagents.orchestrator.hooks import EventHooks
from backend.tools.registry import ORCHESTRATOR_TOOLS
from backend.tools.user_tool import unwrap_control_signal
from backend.workspace.ledger import rehydrate_counters
from backend.workspace.models import Contract, PendingQuestion

__all__ = ["SliceOutcome", "build_orchestrator", "resume_run", "start_run"]

log = get_logger(__name__)

ORCHESTRATOR_RUN_CONFIG = RunConfig(tracing_disabled=True, trace_include_sensitive_data=False)

Status = Literal["complete", "awaiting_input", "turns_exhausted", "over_budget"]


@dataclass(frozen=True)
class SliceOutcome:
    status: Status
    message: str
    questions: list[dict[str, object]] | None = None
    call_id: str | None = None
    total_tokens: int = 0
    draft_attempts: int = 0


def build_orchestrator_spec() -> AgentSpec[Any]:
    """The supervisor, as data.

    `parallel_tool_calls=False` serialises workspace writes, and stops the model batching
    several `ask_user` calls into one turn — only the first would ever be answered.

    Unlike the sub-agents, this spec's *execution* is not behind the port: `_run_slice`
    below drives `Runner.run` itself because it owns conversation history, event hooks,
    and the suspend/resume outcome mapping. Its definition is still a spec, so a future
    runtime inherits the tool list and prompt without re-deriving them.
    """
    return AgentSpec(
        name="orchestrator",
        prompt=load_prompt("orchestrator"),
        model=settings.orchestrator_model,
        tools=(
            *(tool.name for tool in ORCHESTRATOR_TOOLS),
            "recall_memory",
            "remember_fact",
            "resolve_memory_conflict",
            "forget_memory",
            "run_drafting_agent",
            "run_judge_agent",
            "finalize_contract",
            "export_docx",
            "ask_user",
        ),
        temperature=0.2,
    )


def build_orchestrator(model: str | Model | None = None) -> Agent[RunContext]:
    return OpenAIAgentsRuntime(model).build_agent(build_orchestrator_spec())


def _session(engine: AsyncEngine, contract_id: uuid.UUID) -> SQLAlchemySession:
    """Conversation history, in the same Postgres. Survives a pod restart."""
    return SQLAlchemySession(str(contract_id), engine=engine, create_tables=True)


async def _set_status(context: RunContext, status: str) -> None:
    async with context.session_factory() as session:
        contract = await session.get(Contract, context.contract_id)
        if contract is not None:
            contract.status = status
            await session.commit()


async def _run_slice(
    agent: Agent[RunContext],
    context: RunContext,
    engine: AsyncEngine,
    user_message: str,
    publisher: EventPublisher | None = None,
) -> SliceOutcome:
    """One `Runner.run`, from a user message to the next stopping point.

    When a `publisher` is supplied, every tool call, tool result and plan revision is appended
    to `run_events` before any client is notified. The outcome is also emitted as a terminal
    event, so a client that connects late still learns how the run ended.
    """
    await rehydrate_counters(context)  # the DB is the ledger; RunContext is a cache
    hooks = EventHooks(publisher) if publisher else None

    try:
        result = await Runner.run(
            agent,
            input=user_message,
            context=context,
            max_turns=settings.max_turns,
            session=_session(engine, context.contract_id),
            run_config=ORCHESTRATOR_RUN_CONFIG,
            hooks=hooks,
        )
    except MaxTurnsExceeded:
        await _set_status(context, "failed")
        return await _finish(
            context,
            publisher,
            "turns_exhausted",
            f"The agent used all {settings.max_turns} turns without finishing. "
            "The best draft so far is in the workspace and needs a human.",
        )
    except Exception as exc:  # the runner wraps control signals; unwrap before deciding
        signal = unwrap_control_signal(exc)
        if isinstance(signal, SuspendRun):
            await _set_status(context, "awaiting_input")
            return await _finish(
                context,
                publisher,
                "awaiting_input",
                "Waiting for the user to answer.",
                questions=signal.questions,
                call_id=signal.call_id,
            )
        if isinstance(signal, CostCeilingExceeded):
            await _set_status(context, "failed")
            return await _finish(
                context,
                publisher,
                "over_budget",
                f"The run spent {signal.spent_cents:.0f} tokens of its "
                f"{signal.ceiling_cents:.0f} budget and stopped.",
            )
        await _set_status(context, "failed")
        if publisher:
            # Class name only. `str(exc)` on a database or provider error carries the
            # statement, its bound parameters, or the DSN — see `format_tool_error`.
            await publisher.emit("error", reason=type(exc).__name__)
        raise

    await _set_status(context, "ready")
    return await _finish(context, publisher, "complete", str(result.final_output or "").strip())


_TERMINAL_EVENT: dict[Status, str] = {
    "complete": "complete",
    "awaiting_input": "input_required",
    "turns_exhausted": "error",
    "over_budget": "error",
}


async def _finish(
    context: RunContext,
    publisher: EventPublisher | None,
    status: Status,
    message: str,
    *,
    questions: list[dict[str, object]] | None = None,
    call_id: str | None = None,
) -> SliceOutcome:
    outcome = _outcome(context, status, message, questions=questions, call_id=call_id)
    if publisher:
        await publisher.emit(
            _TERMINAL_EVENT[status],  # type: ignore[arg-type]
            status=status,
            message=message,
            questions=questions,
            total_tokens=outcome.total_tokens,
            draft_attempts=outcome.draft_attempts,
        )
    return outcome


def _outcome(
    context: RunContext,
    status: Status,
    message: str,
    *,
    questions: list[dict[str, object]] | None = None,
    call_id: str | None = None,
) -> SliceOutcome:
    log.info(
        "slice status=%s attempts=%d tokens=%d",
        status,
        context.draft_attempts,
        context.total_tokens,
    )
    return SliceOutcome(
        status=status,
        message=message,
        questions=questions,
        call_id=call_id,
        total_tokens=context.total_tokens,
        draft_attempts=context.draft_attempts,
    )


async def start_run(
    contract_id: uuid.UUID,
    request: str,
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    model: str | Model | None = None,
    publisher: EventPublisher | None = None,
) -> SliceOutcome:
    context = RunContext(contract_id=contract_id, session_factory=session_factory)
    return await _run_slice(build_orchestrator(model), context, engine, request, publisher)


async def resume_run(
    contract_id: uuid.UUID,
    answers: dict[str, str],
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    model: str | Model | None = None,
    publisher: EventPublisher | None = None,
) -> SliceOutcome:
    """Answer the outstanding questions and continue.

    Idempotent on `(contract_id, call_id)`: a client that retries `POST /answers` does not
    start a second run.
    """
    async with session_factory() as session:
        pending = (
            (
                await session.execute(
                    select(PendingQuestion)
                    .where(
                        PendingQuestion.contract_id == contract_id,
                        PendingQuestion.answered_at.is_(None),
                    )
                    .order_by(PendingQuestion.created_at)
                )
            )
            .scalars()
            .all()
        )

        if not pending:
            raise ValueError(f"contract {contract_id} is not waiting for an answer")

        from sqlalchemy import func as sa_func

        for question in pending:
            question.answers = answers
            question.answered_at = sa_func.now()
        await session.commit()

    context = RunContext(contract_id=contract_id, session_factory=session_factory)
    await _set_status(context, "drafting")

    # A plain user message. The suspending turn was never written to the session, so there is
    # no orphaned tool call to close — and a `function_call_output` here would be an output
    # with no matching call.
    message = "The user answered your questions:\n" + json.dumps(answers, indent=2)
    return await _run_slice(build_orchestrator(model), context, engine, message, publisher)
