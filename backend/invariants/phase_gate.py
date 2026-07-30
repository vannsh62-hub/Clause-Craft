"""The Phase A → Phase B boundary, checked rather than trusted.

Phase B receives a `ContractKnowledgeObject` and nothing else. That is enforced in three
places, deliberately overlapping, because the boundary is the architecture and a boundary
that erodes quietly is worse than one that was never drawn:

1. `run_drafting_engine(cko, ctx)` takes no other knowledge parameter — a signature test
   catches a second one being added to unblock a deadline.
2. An import guard stops Phase B reaching into Phase A for anything but the CKO schema.
3. This module, which checks the CKO is *complete enough* for the mode it claims.

The third exists because the first two only prove that nothing else was passed. They say
nothing about whether what *was* passed is usable. Entering template mode with no
`formatting` satisfies both guards and then silently regenerates the document instead of
editing it — the user uploads a DOCX to preserve its formatting and gets back something
that lost it.
"""

from __future__ import annotations

from backend.schemas.cko import ContractKnowledgeObject
from backend.schemas.errors import ContractToolError

__all__ = ["PhaseGateError", "assert_cko_complete", "missing_requirements"]


class PhaseGateError(ContractToolError):
    """A CKO that Phase B cannot act on."""


def missing_requirements(cko: ContractKnowledgeObject) -> tuple[str, ...]:
    """What this CKO lacks for its declared mode. Empty means it is usable."""
    missing: list[str] = []

    if cko.intent.must_ask:
        missing.append(
            "intent still has unanswered questions; ask the user before drafting "
            f"({', '.join(cko.intent.needs_clarification)})"
        )

    if not cko.resolution.providers:
        missing.append("no knowledge providers were resolved, so there is nothing to draft from")

    if cko.intent.mode == "template":
        if cko.formatting is None:
            missing.append(
                "mode is 'template' but no formatting manifest was captured; drafting would "
                "regenerate the document rather than edit it"
            )
        elif not cko.formatting.blocks:
            missing.append("the formatting manifest is empty, so no section can be preserved")

    if cko.intent.mode == "library_playbook" and not cko.clause_candidates:
        missing.append("mode is 'library_playbook' but no clause candidates were gathered")

    return tuple(missing)


def assert_cko_complete(cko: ContractKnowledgeObject) -> None:
    """Raise unless `cko` is usable by the drafting engine.

    Called at the top of Phase B, before any planning and before any model call. A CKO
    that cannot support drafting should cost nothing to reject.
    """
    missing = missing_requirements(cko)
    if missing:
        raise PhaseGateError(
            "This contract knowledge object is not ready for drafting:\n"
            + "\n".join(f"  - {reason}" for reason in missing)
        )
