"""The drafting engine's entry point.

Its signature is the architecture, written down:

    async def run_drafting_engine(cko: ContractKnowledgeObject, ctx: RunContext) -> DraftOutcome

One knowledge parameter. Not `template=`, not `clause_library=`, not `references=` — those
belong to Phase A, and everything they hold is already in the CKO. `test_phase_b_signature.py`
asserts the parameter set is exactly `{cko, ctx}`, and that test exists for one specific
failure: someone, under deadline, adds a second knowledge argument to "just get the template
through", and the two-phase architecture quietly ends while the diagrams still show it.

The stages themselves — draft planning, transformation planning, drafting, validation, DOCX
generation — arrive in M8 through M13. This milestone establishes the boundary and the
precondition, which is the part that is hard to add back later once call sites have grown
used to reaching around it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from backend.core.run_context import RunContext
from backend.invariants.phase_gate import assert_cko_complete
from backend.phase_b.drafting import DraftResult, draft
from backend.phase_b.finalize import FinalizeDecision, finalize
from backend.phase_b.planning import plan_draft, plan_transformation
from backend.phase_b.recommendation import recommend_clauses
from backend.schemas.cko import ContractKnowledgeObject

__all__ = ["DraftOutcome", "run_drafting_engine"]


@dataclass(frozen=True)
class DraftOutcome:
    """The result of a drafting run.

    A placeholder shape for now — it grows a version id, a validation report and an export
    reference as the stages land. It exists at M7 so the entry point has a real return
    type rather than `None`, which a later stage would have to widen.
    """

    contract_id: str
    ready: bool
    detail: str = ""
    #: Finalized, but carrying fill-in placeholders or other non-blocking flags a reviewer
    #: must see. A `ready` document may still need review; the two are independent.
    needs_review: bool = False


async def run_drafting_engine(cko: ContractKnowledgeObject, ctx: RunContext) -> DraftOutcome:
    """Draft a contract from a Contract Knowledge Object.

    Takes the CKO and the run context. **No other knowledge parameter may be added here.**
    If a stage needs a fact, that fact belongs in the CKO — adding it there is routine;
    adding a parameter here is a design review, because it is the boundary eroding.

    Refuses before doing anything if the CKO is not usable for its mode: template mode
    without a formatting manifest, an intent still carrying unanswered questions, a run
    with no providers. Rejecting a bad CKO must cost nothing.
    """
    assert_cko_complete(cko)

    # Plan the shape, then classify every section, then choose clauses, then draft.
    # The order is load-bearing: `draft` refuses without the transformation plan, so the
    # plan must exist first, and it must be a reasoned artifact rather than an afterthought.
    await plan_draft(cko, ctx)
    await plan_transformation(cko, ctx)
    # Recommendation only has something to do when the CKO carries clause candidates
    # (Mode 3). It is cheap and deterministic, and persists `07` for the audit trail.
    if cko.clause_candidates:
        await recommend_clauses(cko, ctx)
    result = await draft(cko, ctx)

    # Both validators run. A blocker in either refuses finalization — the one path to a
    # document goes through the gates, never around them.
    decision = await finalize(result.markdown, cko, ctx)

    return DraftOutcome(
        contract_id=str(cko.contract_id),
        ready=decision.finalized,
        detail=_detail(result, decision),
        needs_review=decision.needs_review,
    )


def _detail(result: DraftResult, decision: FinalizeDecision) -> str:
    """What happened, in the words the user will read on the blocked card.

    Both the reasons and their fix hints, de-duplicated: a blocker the user cannot act on is
    only half a report, and the same sentence repeated once per finding is worse than one.
    """
    if decision.finalized:
        if decision.flags:
            flags = _unique(f.message for f in decision.flags)
            return "Drafted and ready, with details for you to fill in: " + "; ".join(flags)
        return f"drafted attempt {result.attempt} and finalized"
    # (the "fill in:" marker above is what the UI reads to show the slot list)

    reasons = _unique(f.message for f in decision.blockers)
    hints = _unique(f.fix_hint for f in decision.blockers if f.fix_hint)
    detail = f"drafted attempt {result.attempt}, blocked: " + "; ".join(reasons)
    if hints:
        detail += " — " + " ".join(hints)
    return detail


def _unique(values: Iterable[str]) -> list[str]:
    """Distinct, in first-seen order."""
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return list(seen)
