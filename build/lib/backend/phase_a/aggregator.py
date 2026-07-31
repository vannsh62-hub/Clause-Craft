"""Merging every provider's contribution into one Contract Knowledge Object.

The last step of Phase A, and deliberately the dullest: a merge, not a judgement. There is
no model call here and there is no I/O in the merge itself. Everything interesting already
happened — the providers gathered knowledge, the agents interpreted it — and this assembles
the result and records where sources disagreed.

## Precedence, and why conflicts are recorded rather than resolved away

When two providers supply the same fact with different values, precedence decides the
winner: `playbook > clause_library > template > reference > llm`. That much is
unavoidable — a single CKO can hold one payment term.

What is *not* unavoidable, and is the whole point of this module, is that the loser is
written down. Every conflict lands in `cko.conflicts` with both values and both
provenances. Silent precedence is how a playbook violation ships: the playbook wins, the
template's contradicting figure is discarded without trace, and the discrepancy first
surfaces during negotiation — across the table from the counterparty — instead of during
review. A recorded conflict is a line in the validation report; a resolved-away one is a
surprise.

## Why this is a pure function

`aggregate` takes the contributions and returns a CKO. It reads nothing and writes nothing.
That makes its behaviour a table — these contributions in, these conflicts out — which is
exactly how it is tested, and it means the precedence logic can be reasoned about without a
database or a model anywhere near it. Persisting the CKO is a separate, thin step.
"""

from __future__ import annotations

import uuid
from typing import Any

from backend.knowledge.base import precedence_of
from backend.schemas.cko import (
    CKO_SCHEMA_VERSION,
    ClauseCandidate,
    ConfidenceReport,
    ContractKnowledgeObject,
    ContractMetadata,
    Definition,
    KnowledgeConflict,
    KnowledgeGraph,
    Provenance,
    SemanticSection,
)
from backend.schemas.intent import IntentObject, Party, ResolutionPlan
from backend.schemas.playbook import BusinessRule, PlaybookRequirement
from backend.schemas.provider import KnowledgeContribution
from backend.schemas.template import FormattingManifest

__all__ = ["aggregate"]

#: Metadata fields where two providers can genuinely disagree, and where the disagreement
#: matters enough to record. Provenance-only fields (which provider said what) are not here.
_METADATA_FIELDS: tuple[str, ...] = (
    "contract_name",
    "version",
    "effective_date",
    "duration",
    "country",
    "language",
    "currency",
    "notice_period_days",
    "payment_terms_days",
    "jurisdiction",
    "governing_law",
    "contract_value",
)


def aggregate(
    contributions: tuple[KnowledgeContribution, ...],
    intent: IntentObject,
    resolution: ResolutionPlan,
    *,
    contract_id: uuid.UUID,
    tenant_id: uuid.UUID | None = None,
) -> ContractKnowledgeObject:
    """Merge `contributions` into a CKO, recording every conflict.

    Contributions are expected in precedence order (the gather step returns them that way),
    but this does not trust that — it sorts by precedence so the winner of a conflict does
    not depend on the caller having ordered its input correctly.
    """
    ordered = tuple(sorted(contributions, key=lambda c: (precedence_of(c.provider), c.provider)))

    conflicts: list[KnowledgeConflict] = []
    metadata = _merge_metadata(ordered, conflicts)

    return ContractKnowledgeObject(
        schema_version=CKO_SCHEMA_VERSION,
        contract_id=contract_id,
        tenant_id=tenant_id,
        resolution=resolution,
        intent=intent,
        metadata=metadata,
        parties=_parties(intent),
        definitions=_collect(ordered, "definitions", Definition),
        sections=_collect(ordered, "sections", SemanticSection),
        clause_candidates=_collect(ordered, "clause_candidates", ClauseCandidate),
        formatting=_first_formatting(ordered),
        placeholders=(),
        source_storage_key=_first_source_key(ordered),
        playbook_rules=_collect(ordered, "requirements", PlaybookRequirement),
        business_rules=_collect(ordered, "business_rules", BusinessRule),
        reference_knowledge=_collect(ordered, "reference_knowledge", KnowledgeGraph),
        risk_signals=(),
        missing_sections=(),
        conflicts=tuple(conflicts),
        confidence=_confidence(ordered),
    )


def _merge_metadata(
    ordered: tuple[KnowledgeContribution, ...],
    conflicts: list[KnowledgeConflict],
) -> ContractMetadata:
    """Take each metadata field from the highest-precedence provider that supplied it.

    A lower-precedence provider that supplied a *different* value for a field already
    decided is a conflict: recorded, with both values and both provenances, then
    discarded. A lower provider supplying the *same* value, or nothing, is not a conflict.
    """
    winners: dict[str, Any] = {}
    winning_provenance: dict[str, Provenance] = {}

    for contribution in ordered:
        if contribution.metadata is None:
            continue
        for field in _METADATA_FIELDS:
            value = getattr(contribution.metadata, field)
            if value is None:
                continue
            if field not in winners:
                winners[field] = value
                winning_provenance[field] = contribution.provenance
            elif winners[field] != value:
                winner_name = winning_provenance[field].provider
                loser_name = contribution.provenance.provider
                conflicts.append(
                    KnowledgeConflict(
                        field=f"metadata.{field}",
                        winning_value=str(winners[field]),
                        winning_provenance=winning_provenance[field],
                        losing_value=str(value),
                        losing_provenance=contribution.provenance,
                        applied_precedence=f"{winner_name} > {loser_name}",
                    )
                )

    return ContractMetadata(**winners)


def _collect(
    ordered: tuple[KnowledgeContribution, ...],
    field: str,
    _model: type[Any],
) -> tuple[Any, ...]:
    """Concatenate a list-valued field across all contributions, in precedence order.

    Lists are additive, not exclusive: two providers each contributing clause candidates
    both contribute them. Precedence only decides order here, so the higher-authority
    provider's items come first — which is what a downstream ranker will expect.
    """
    out: list[Any] = []
    for contribution in ordered:
        out.extend(getattr(contribution, field))
    return tuple(out)


def _first_formatting(
    ordered: tuple[KnowledgeContribution, ...],
) -> FormattingManifest | None:
    """The formatting manifest, from the highest-precedence provider that has one.

    Only the template provider supplies formatting today, so there is never a contest —
    but taking the first by precedence is the rule that stays correct if a second source
    of formatting ever appears.
    """
    for contribution in ordered:
        if contribution.formatting is not None:
            return contribution.formatting
    return None


def _first_source_key(ordered: tuple[KnowledgeContribution, ...]) -> str | None:
    """The uploaded document's storage key, from the highest-precedence provider with one.

    Travels with `formatting` and from the same provider — the two describe the same
    document, and Phase B needs both to edit it in place.
    """
    for contribution in ordered:
        if contribution.source_storage_key is not None:
            return contribution.source_storage_key
    return None


def _parties(intent: IntentObject) -> tuple[Party, ...]:
    """Parties come from intent.

    Deliberately not from providers: who is contracting is established when the request is
    understood, and a provider inventing a party would be inventing a fact about the deal.
    """
    return intent.parties


def _confidence(ordered: tuple[KnowledgeContribution, ...]) -> ConfidenceReport:
    """Per-provider confidence, plus an overall.

    Overall is the minimum rather than the mean: a CKO is only as trustworthy as its least
    trustworthy component, and averaging lets a confident metadata extraction paper over a
    shaky classification.
    """
    components = tuple((c.provider, c.confidence) for c in ordered)
    overall = min((c.confidence for c in ordered), default=1.0)
    return ConfidenceReport(overall=overall, components=components)
