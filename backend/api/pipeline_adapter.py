"""Run the two-phase pipeline behind the runner's `start_run` / `resume_run` surface.

`api/runner.py` was written against the old LLM orchestrator, which returned a `SliceOutcome`,
set the contract's status itself, wrote `final.md` / `draft_v{n}.md` into the workspace, and
emitted the terminal SSE event. `run_pipeline` (Phase A → CKO → Phase B) does none of those —
it returns a `PipelineOutcome` and leaves the wiring to its caller. This module is that
wiring: it presents the exact surface the runner already imports, so the swap is one import
line, and it fills the four gaps the pipeline leaves open.

Four gaps, made explicit:

1. **Contract status.** The `POST /answers` gate requires `contract.status == "awaiting_input"`,
   and the UI reads the status. The pipeline never sets it; we do, from the outcome.
2. **Terminal event.** The client's stream stops on `complete` / `input_required` / `error`.
   The pipeline emits stage events but no terminal one, so we emit it here.
3. **Workspace markdown.** The preview reads `final.md` (or the newest `draft_v{n}.md`) from
   the VFS. Phase B stores the draft only in `ContractVersion.markdown` and writes a `.docx`,
   so we mirror that markdown into the VFS where the UI looks for it.
4. **`finalized_at`.** Phase B validates but does not stamp the winning version finalized. The
   `finalized` flag on the contracts list and the one-final-version guarantee both depend on
   that stamp, so a completed run sets it here (and clears any prior finalized row, honouring
   the single-final constraint).

Resume differs from the old path by design: there is no long-lived agent conversation to
re-enter, only a request to re-understand. So resume folds the answers into the original
request and runs the pipeline again — Phase A's intent stage now has what it was missing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from backend.api.events import EventPublisher
from backend.core.config import settings
from backend.core.logging import get_logger
from backend.core.run_context import RunContext
from backend.phase_a.consent import QUESTION_NAME, is_affirmative, record_consent
from backend.pipeline import PipelineOutcome, run_pipeline
from backend.workspace.models import Contract, ContractVersion, PendingQuestion

__all__ = ["SliceOutcome", "resume_run", "start_run"]

log = get_logger(__name__)

#: Where the UI's preview loader looks for the rendered draft.
FINAL_PATH = "final.md"
DRAFT_PATH = "draft_v{n}.md"

Status = Literal["complete", "awaiting_input", "turns_exhausted", "over_budget"]


@dataclass(frozen=True)
class SliceOutcome:
    """The runner's expected outcome shape — identical to the old orchestrator's.

    Keeping the fields the runner already reads (`status`, and the counters it logs) means
    `api/runner.py` maps our result exactly as before; `_STATUS` there turns each status into
    a run status.
    """

    status: Status
    message: str
    questions: list[dict[str, object]] | None = None
    call_id: str | None = None
    total_tokens: int = 0
    draft_attempts: int = 0


#: Pipeline outcome -> (contract status, run outcome status, terminal SSE event).
#: `blocked` is a finished run with a retained draft, so it reports `complete` — the draft and
#: the blocking reasons are both surfaced, never withheld.
_MAP: dict[str, tuple[str, Status, str]] = {
    "complete": ("ready", "complete", "complete"),
    "blocked": ("ready", "complete", "complete"),
    "awaiting_input": ("awaiting_input", "awaiting_input", "input_required"),
    "over_budget": ("failed", "over_budget", "error"),
}


async def _set_status(ctx: RunContext, status: str) -> None:
    async with ctx.session_factory() as session:
        contract = await session.get(Contract, ctx.contract_id)
        if contract is not None:
            contract.status = status
            await session.commit()


async def _record_contract_type(ctx: RunContext) -> None:
    """Copy the contract type Phase A determined onto the contract row.

    Phase A writes it into the intent artifact and nothing else reads it back, so without
    this the row stays null for every pipeline run — and the type is what the contracts list,
    the export's document title and the UI all key off. Best-effort: a run that produced no
    intent (it failed early) simply leaves the row alone.
    """
    from backend.artifacts import Artifact, ArtifactStore
    from backend.schemas.intent import IntentObject

    store = ArtifactStore(ctx.session_factory, ctx.contract_id)
    if not await store.exists(Artifact.INTENT):
        return
    intent = await store.load(Artifact.INTENT)
    if not isinstance(intent, IntentObject):
        return

    async with ctx.session_factory() as session:
        contract = await session.get(Contract, ctx.contract_id)
        if contract is not None and not contract.contract_type:
            contract.contract_type = intent.contract_type
            await session.commit()

async def _record_contract_variables(ctx: RunContext) -> None:
    """Copy resolved deal terms and party names onto the contract row.

    This is what `POST /clauses/{id}/render` reads to autofill an inserted clause
    (`disclosing_party`, `effective_date`, and so on) instead of leaving every placeholder
    as `{{ name }}`. Without it `Contract.variables` stays `{}` for every run. Sibling of
    `_record_contract_type` above, reading the same intent artifact — best-effort in the
    same way: a run that produced no intent simply leaves the row alone.
    """
    from backend.artifacts import Artifact, ArtifactStore
    from backend.core.variable_aliases import resolve_variables
    from backend.schemas.intent import IntentObject

    store = ArtifactStore(ctx.session_factory, ctx.contract_id)
    if not await store.exists(Artifact.INTENT):
        return
    intent = await store.load(Artifact.INTENT)
    if not isinstance(intent, IntentObject):
        return

    deal_terms = {term.name: term.value for term in intent.deal_terms}
    parties = {party.role: party.name for party in intent.parties}
    resolved = resolve_variables(deal_terms, parties)
    if not resolved:
        return

    async with ctx.session_factory() as session:
        contract = await session.get(Contract, ctx.contract_id)
        if contract is not None:
            # Merge, existing values win: a resume pass may know less than an earlier one
            # did if the user's answer didn't restate everything already captured.
            contract.variables = {**resolved, **(contract.variables or {})}
            await session.commit()


async def _mirror_markdown(ctx: RunContext, *, finalize: bool, needs_review: bool = False) -> int:
    """Copy the newest version's markdown into the VFS where the preview reads it.

    On a completed run the draft becomes `final.md` and its version is stamped finalized; a
    blocked draft is written as `draft_v{n}.md` so it is still previewable. Returns the
    attempt number, or 0 if there was no version to mirror.
    """
    async with ctx.session_factory() as session:
        version = (
            (
                await session.execute(
                    select(ContractVersion)
                    .where(ContractVersion.contract_id == ctx.contract_id)
                    .order_by(ContractVersion.attempt.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if version is None:
            return 0

        # A finalized draft that still carries fill-in placeholders is flagged, not hidden:
        # the UI shows "ready, N details to fill" rather than pretending it is signed.
        version.needs_human_review = needs_review

        if finalize:
            # Honour the one-finalized-version-per-contract index: clear any prior final row
            # before stamping this one.
            others = (
                (
                    await session.execute(
                        select(ContractVersion).where(
                            ContractVersion.contract_id == ctx.contract_id,
                            ContractVersion.finalized_at.is_not(None),
                            ContractVersion.id != version.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for other in others:
                other.finalized_at = None
            version.finalized_at = sa_func.now()

        from backend.workspace.store import WorkspaceStore

        path = FINAL_PATH if finalize else DRAFT_PATH.format(n=version.attempt)
        await WorkspaceStore(session).write(ctx.contract_id, path, version.markdown or "")
        await session.commit()
        return version.attempt


async def _apply(
    outcome: PipelineOutcome, ctx: RunContext, publisher: EventPublisher | None
) -> SliceOutcome:
    """Turn a pipeline outcome into a slice outcome, doing the four gap fixes on the way."""
    contract_status, slice_status, event = _MAP[outcome.status]
    await _set_status(ctx, contract_status)
    await _record_contract_type(ctx)
    await _record_contract_variables(ctx)

    if outcome.status in ("complete", "blocked"):
        await _mirror_markdown(
            ctx,
            finalize=outcome.status == "complete",
            needs_review=outcome.needs_review,
        )

    if publisher:
        await publisher.emit(
            event,  # type: ignore[arg-type]
            status=slice_status,
            message=outcome.message,
            questions=outcome.questions,
            needs_review=outcome.needs_review,
        )

    return SliceOutcome(
        status=slice_status,
        message=outcome.message,
        questions=outcome.questions,
        call_id=outcome.call_id,
    )


async def start_run(
    contract_id: uuid.UUID,
    request: str,
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    model: str | None = None,
    publisher: EventPublisher | None = None,
) -> SliceOutcome:
    """Run the pipeline for a fresh request. `engine` and `model` are unused here (the pipeline
    manages its own runtime) but kept so the runner's call site is unchanged."""
    ctx = RunContext(contract_id=contract_id, session_factory=session_factory)
    outcome = await run_pipeline(request, ctx, publisher)
    return await _apply(outcome, ctx, publisher)


async def resume_run(
    contract_id: uuid.UUID,
    answers: dict[str, str],
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    model: str | None = None,
    publisher: EventPublisher | None = None,
) -> SliceOutcome:
    """Fold the answers into the original request and re-run the pipeline.

    Marks the outstanding questions answered first, so a client that retries `POST /answers`
    after the run has moved on does not start a second run.
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
        for question in pending:
            question.answers = answers
            question.answered_at = sa_func.now()
        contract = await session.get(Contract, contract_id)
        original_request = contract.request if contract is not None else ""
        await session.commit()

        # Every answered round, not just this one. A resume re-runs Phase A from the start,
        # so anything left out here is a thing the user already told us and the agent will
        # ask for again.
        transcript, rounds = await _answer_transcript(session, contract_id)

    ctx = RunContext(contract_id=contract_id, session_factory=session_factory)

    # A consent answer is a decision, not drafting input. Record it here, where the answers
    # arrive as data — recovering it later by parsing the folded prose would be guessing at
    # something the user stated plainly.
    if QUESTION_NAME in answers:
        granted = is_affirmative(answers[QUESTION_NAME])
        await record_consent(ctx, granted=granted, answer=answers[QUESTION_NAME])
        if not granted:
            return await _declined(ctx, publisher)

    await _set_status(ctx, "drafting")

    outcome = await run_pipeline(_fold(original_request, transcript, rounds), ctx, publisher)
    return await _apply(outcome, ctx, publisher)


#: The same allowance the intent stage enforces (`settings.max_ask_rounds`). Here it only
#: changes what the request *says*; the stop itself is enforced in code in `phase_a/intent.py`,
#: because a sentence asking an agent to stop is not a stop condition.
MAX_ASK_ROUNDS = settings.max_ask_rounds


async def _answer_transcript(
    session: AsyncSession, contract_id: uuid.UUID
) -> tuple[list[str], int]:
    """Every question this contract has asked, paired with the answer it got.

    Pairing matters as much as accumulating. The questions are named positionally —
    `clarification_1`, `clarification_2` — so the same key means a different question in every
    round, and an answer handed back as `{"clarification_1": "3 years"}` tells the model
    nothing. Restating the question text is what makes the answer usable.
    """
    rows = (
        (
            await session.execute(
                select(PendingQuestion)
                .where(
                    PendingQuestion.contract_id == contract_id,
                    PendingQuestion.answered_at.is_not(None),
                )
                .order_by(PendingQuestion.created_at)
            )
        )
        .scalars()
        .all()
    )

    lines: list[str] = []
    for row in rows:
        given = row.answers or {}
        for item in row.questions or []:
            name = str(item.get("name", ""))
            answer = str(given.get(name, "")).strip()
            if not answer:
                continue
            lines.append(f"- {item.get('question', name)}\n  ANSWER: {answer}")
    return lines, len(rows)


def _fold(original_request: str, transcript: list[str], rounds: int) -> str:
    """The request as the pipeline should now see it: what was asked, plus what is known."""
    if not transcript:
        return original_request

    folded = (
        f"{original_request}\n\n"
        "The user has already answered these questions. Treat every answer below as an "
        "established fact of this deal, and do not ask any of them again:\n" + "\n".join(transcript)
    )

    if rounds >= MAX_ASK_ROUNDS:
        folded += (
            f"\n\nYou have stopped to ask {rounds} times already. Ask nothing further. "
            "Draft the contract from the information above; where a detail is genuinely still "
            "missing, write the provision so it refers to the relevant schedule rather than "
            "asking again or inventing a figure."
        )
    return folded


async def _declined(ctx: RunContext, publisher: EventPublisher | None) -> SliceOutcome:
    """The user declined to have the contract drafted from model knowledge.

    Ends the run cleanly rather than drafting anyway. Reported as a completed run with a
    plain explanation, because nothing failed — the system asked, and was told no.
    """
    message = (
        "No contract was drafted. There are no counsel-approved clauses for this contract "
        "type, and you asked not to draft it from the model's own knowledge. Add clauses for "
        "this type in the Clause Library, or upload a document to use as a template."
    )
    await _set_status(ctx, "ready")
    if publisher:
        await publisher.emit("complete", status="complete", message=message, questions=None)
    return SliceOutcome(status="complete", message=message)