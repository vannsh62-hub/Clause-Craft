"""Consent to draft without approved clauses.

The library covers a handful of contract types. Everything else — and every type whose clause
folder is empty — is drafted from the model's own legal knowledge, which is a different
proposition entirely: the wording has not been reviewed by anyone. Until now the system did
that silently, so a user could not tell an NDA assembled from counsel-approved text apart from
one the model wrote from memory.

So it asks, once, and records the answer.

## Once per contract, and why that needs a marker

A suspension ends the run slice; answering starts a *new* run that re-enters Phase A from the
top. So "have we already asked?" cannot live in memory — it has to be on disk, keyed to the
contract. The marker is a workspace file, which is the same durability the pending questions
themselves rely on.

The answer is recorded by the code that receives it (`api/pipeline_adapter.resume_run`), not
by parsing it back out of the folded request text. A consent decision inferred from prose is a
consent decision that can be misread.
"""

from __future__ import annotations

import json

from backend.core.logging import get_logger
from backend.core.run_context import RunContext
from backend.schemas.intent import IntentObject, ResolutionPlan
from backend.schemas.question import Question
from backend.workspace.store import WorkspaceStore

__all__ = [
    "CONSENT_PATH",
    "QUESTION_NAME",
    "consent_question",
    "has_consent",
    "is_affirmative",
    "needs_consent",
    "record_consent",
]

log = get_logger(__name__)

#: Where the decision is kept. Outside `clauses/`, so it is writable.
CONSENT_PATH = "consent/model-knowledge.json"

#: The stable name the answer comes back under. The resume path keys off this exact string.
QUESTION_NAME = "draft_without_library"

#: Sources that make a draft *backed* by something other than the model's own knowledge.
_BACKED_BY = ("clause_library", "template")

_AFFIRMATIVE = {"y", "yes", "ok", "okay", "sure", "proceed", "continue", "go ahead", "true", "1"}


def needs_consent(plan: ResolutionPlan) -> bool:
    """True when nothing but the model's own knowledge would supply the contract's words.

    The playbook and the reference provider do not count: a playbook contributes *rules*
    about what must be present, not clause text, and a reference document is analysed for
    patterns and never copied. Neither makes the drafted wording reviewed.
    """
    return not any(source in plan.providers for source in _BACKED_BY)


async def has_consent(ctx: RunContext) -> bool:
    async with ctx.session_factory() as session:
        return await WorkspaceStore(session).exists(ctx.contract_id, CONSENT_PATH)


async def record_consent(ctx: RunContext, *, granted: bool, answer: str = "") -> None:
    """Write the decision so the resumed run does not ask again."""
    payload = json.dumps({"granted": granted, "answer": answer}, indent=2, sort_keys=True)
    async with ctx.session_factory() as session:
        await WorkspaceStore(session).write(ctx.contract_id, CONSENT_PATH, payload)
        await session.commit()
    log.info("model-knowledge consent granted=%s", granted)


def is_affirmative(answer: str) -> bool:
    """Whether a free-text answer means yes.

    Deliberately conservative: anything that is not recognisably a yes is treated as a no,
    because the failure of reading "no, not without review" as consent is far worse than the
    failure of asking again.
    """
    cleaned = answer.strip().lower().rstrip(".!")
    if not cleaned:
        return False
    if cleaned in _AFFIRMATIVE:
        return True
    return cleaned.split()[0] in _AFFIRMATIVE


def consent_question(intent: IntentObject) -> Question:
    contract_type = intent.contract_type or "contract"
    return Question(
        name=QUESTION_NAME,
        question=(
            f"I don't have counsel-approved clauses for a {contract_type} agreement, so I would "
            "be drafting it from the model's own legal knowledge — the wording would not come "
            "from your approved library and has not been reviewed by a lawyer. "
            "Shall I go ahead? (yes / no)"
        ),
        type="text",
    )
