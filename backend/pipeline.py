"""The whole system as one call: request in, finalized (or blocked) contract out.

Phase A produces the Contract Knowledge Object; Phase B drafts and validates from it. This
driver runs both, emits a stage event at each step so the existing SSE surface has something
to stream, and returns an outcome the API runner can map to a run status.

It is the new run path — the replacement for the LLM orchestrator in
`backend/subagents/orchestrator/deep_agent.py`. The shape of its outcome deliberately
matches `SliceOutcome`'s statuses (`complete`, `awaiting_input`, `over_budget`) so the swap
at the API layer is a substitution, not a rewrite of the runner.

## Suspension

Phase A's intent stage may stop the run to ask the user. That surfaces here as a
`SuspendRun`, exactly as it did for the old orchestrator, and is reported as
`awaiting_input`. The resume path re-enters with the answers folded into the request — the
one place the new pipeline's resume differs from the old, and it differs because there is no
long-lived agent conversation to resume into, only a request to re-understand.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.api.events import EventPublisher
from backend.core.logging import get_logger
from backend.core.run_context import RunContext
from backend.phase_a.pipeline import run_phase_a
from backend.phase_b.engine import run_drafting_engine
from backend.schemas.errors import CostCeilingExceeded, SuspendRun
from backend.tools.user_tool import unwrap_control_signal

__all__ = ["PipelineOutcome", "run_pipeline"]

log = get_logger(__name__)

PipelineStatus = Literal["complete", "blocked", "awaiting_input", "over_budget"]


@dataclass(frozen=True)
class PipelineOutcome:
    """How a pipeline run ended.

    `complete` means a document was finalized; `blocked` means it was drafted but a gate
    refused it (the draft is retained, the reasons are in the validation artifacts).
    `awaiting_input` carries the questions the user must answer.
    """

    status: PipelineStatus
    message: str
    questions: list[dict[str, object]] | None = None
    call_id: str | None = None
    #: The document is finalized but carries fill-in placeholders a human should complete.
    needs_review: bool = False


async def run_pipeline(
    request: str,
    ctx: RunContext,
    publisher: EventPublisher | None = None,
) -> PipelineOutcome:
    """Run Phase A then Phase B, emitting a stage event at each step.

    Returns an outcome; never raises for the ordinary control paths (a question, a spent
    budget). Only a genuine fault propagates, and the runner turns that into a failed run.
    """
    try:
        await _stage(publisher, "phase_a", "started")
        cko = await run_phase_a(request, ctx)
        await _stage(publisher, "phase_a", "complete", clause_candidates=len(cko.clause_candidates))

        await _stage(publisher, "phase_b", "started")
        outcome = await run_drafting_engine(cko, ctx)
        await _stage(publisher, "phase_b", "complete", finalized=outcome.ready)
    except Exception as exc:  # a control signal reaches us as itself or wrapped
        signal = unwrap_control_signal(exc)
        if isinstance(signal, SuspendRun):
            await _stage(publisher, "intent", "awaiting_input")
            return PipelineOutcome(
                status="awaiting_input",
                message="Waiting for the user to answer.",
                questions=signal.questions,
                call_id=signal.call_id,
            )
        if isinstance(signal, CostCeilingExceeded):
            return PipelineOutcome(
                status="over_budget",
                message=f"The run spent its token budget ({signal.ceiling_cents:.0f}) and stopped.",
            )
        if publisher:
            await publisher.emit("error", reason=type(exc).__name__)
        raise

    status: PipelineStatus = "complete" if outcome.ready else "blocked"
    return PipelineOutcome(status=status, message=outcome.detail, needs_review=outcome.needs_review)


async def _stage(
    publisher: EventPublisher | None, name: str, status: str, **detail: object
) -> None:
    if publisher:
        await publisher.stage(name, status, **detail)
