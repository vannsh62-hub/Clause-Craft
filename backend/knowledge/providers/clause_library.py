"""The approved clause library as a knowledge source.

Mode 3's foundation, and spec 01's original behaviour, now one provider among several. It
retrieves the approved clauses for a contract type and jurisdiction and describes each as a
`ClauseCandidate` — the same shape the classification agent produces from a template, so the
two are interchangeable to everything downstream.

## Retrieval is a lookup, not a search

The library is a dozen documents versioned in git. `clauses_for(type, jurisdiction)` is an
exact filtered lookup, which is faster, reproducible, and more accurate than embedding
similarity for "give me every approved NDA clause". A model call to decide something already
decided by metadata is cost and a failure mode, not intelligence — which is why this
provider, like the playbook, is deterministic.

## Category comes from the clause id

A library clause carries no taxonomy category, but its id encodes one: `nda.confidentiality`
maps to `confidentiality`. The suffix is mapped to a taxonomy id where one exists and
recorded as `subcategory` regardless, so nothing is lost. An unmapped suffix becomes `other`
with the suffix preserved — a category is never invented to fill the field.

## Precedence

`clause_library` outranks `template`, `reference` and `llm` (only `playbook` is higher). An
approved clause is authoritative: it was reviewed and merged, which is a stronger warrant
than anything extracted from an uploaded document.
"""

from __future__ import annotations

from backend.clauselib.loader import clauses_for
from backend.core.logging import get_logger
from backend.core.run_context import RunContext
from backend.invariants.taxonomy import is_known
from backend.knowledge.registry import register_provider
from backend.schemas.cko import ClauseCandidate, Provenance, SourceRef
from backend.schemas.clause import Clause
from backend.schemas.intent import IntentObject
from backend.schemas.provider import KnowledgeContribution

__all__ = ["ClauseLibraryProvider", "clause_to_candidate"]

log = get_logger(__name__)

#: Clause-id suffixes whose taxonomy id differs from the suffix itself. Suffixes that already
#: match a taxonomy id (`confidentiality`, `governing_law`, `non_solicitation`, `definitions`)
#: need no entry. An unmapped suffix that is not itself a taxonomy id becomes `other`.
_SUFFIX_TO_CATEGORY = {
    "duration": "term_and_duration",
    "obligations": "confidentiality",  # NDA "obligations of the receiving party"
    "signatures": "execution",
    "payment": "fees_and_payment",
    "scope": "scope_of_services",
    "liability": "limitation_of_liability",
    "termination": "termination",
}


def _category_of(clause: Clause) -> tuple[str, str | None]:
    """Return `(taxonomy category, subcategory)` for a library clause.

    The suffix is always kept as `subcategory`, so mapping to `other` loses nothing — a
    reviewer still sees which library clause it was.
    """
    suffix = clause.id.split(".", 1)[-1]
    mapped = _SUFFIX_TO_CATEGORY.get(suffix, suffix)
    if is_known(mapped):
        return mapped, suffix
    return "other", suffix


def clause_to_candidate(clause: Clause) -> ClauseCandidate:
    """Describe a library clause as a `ClauseCandidate`.

    High confidence and low risk by default: an approved clause has been reviewed, which is
    exactly what those fields mean. `applicability` carries the clause's jurisdictions and
    contract types so the ranker (M12) can score jurisdiction and industry fit.
    """
    category, subcategory = _category_of(clause)
    return ClauseCandidate(
        category=category,
        subcategory=subcategory,
        purpose=clause.title,
        applicability=(*clause.jurisdictions, *clause.contract_types),
        risk="low",
        mandatory=clause.required,
        negotiable=not clause.required,
        source_ref=SourceRef(provider="clause_library", clause_id=clause.id),
        confidence=1.0,
    )


class ClauseLibraryProvider:
    """Approved clauses for the contract type."""

    name = "clause_library"

    async def available(self, intent: IntentObject, ctx: RunContext) -> bool:
        """Are there approved clauses for this contract type and jurisdiction?"""
        jurisdiction = intent.jurisdiction or "IN"
        return bool(clauses_for(intent.contract_type, jurisdiction))

    async def contribute(self, intent: IntentObject, ctx: RunContext) -> KnowledgeContribution:
        jurisdiction = intent.jurisdiction or "IN"
        clauses = clauses_for(intent.contract_type, jurisdiction)
        candidates = tuple(clause_to_candidate(c) for c in clauses)
        log.info(
            "clause_library type=%s jurisdiction=%s clauses=%d",
            intent.contract_type,
            jurisdiction,
            len(candidates),
        )
        return KnowledgeContribution(
            provider=self.name,
            provenance=Provenance(
                provider=self.name,
                locator=f"{intent.contract_type}@{jurisdiction}",
            ),
            clause_candidates=candidates,
        )


register_provider(ClauseLibraryProvider())
