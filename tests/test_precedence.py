"""Precedence, and the recording of conflicts.

When two providers supply the same fact with different values, one wins — a CKO holds one
payment term. The winner is fixed policy: `playbook > clause_library > template > reference
> llm`. The part that matters, and the reason this is tested as a table, is that the loser
is written down. Silent precedence is how a playbook violation ships: the playbook wins, the
template's contradicting figure vanishes without trace, and the discrepancy surfaces across
a negotiating table instead of in a review.

`aggregate` is a pure function, so every case here is contributions in, CKO out, no database
and no model.
"""

from __future__ import annotations

import uuid

import pytest

from backend.phase_a.aggregator import aggregate
from backend.schemas.cko import ClauseCandidate, ContractMetadata, Provenance
from backend.schemas.intent import IntentObject, Party, ResolutionPlan
from backend.schemas.playbook import PlaybookRequirement
from backend.schemas.provider import KnowledgeContribution

INTENT = IntentObject(contract_type="sla", confidence=0.9, jurisdiction="IN")
RESOLUTION = ResolutionPlan(providers=("playbook", "template", "llm"))
CONTRACT_ID = uuid.uuid4()


def _contribution(
    provider: str,
    *,
    confidence: float = 1.0,
    **kwargs: object,
) -> KnowledgeContribution:
    return KnowledgeContribution(
        provider=provider,
        provenance=Provenance(provider=provider, locator=f"{provider}-src"),
        confidence=confidence,
        **kwargs,  # type: ignore[arg-type]
    )


def _cko(*contributions: KnowledgeContribution):  # type: ignore[no-untyped-def]
    return aggregate(contributions, INTENT, RESOLUTION, contract_id=CONTRACT_ID)


def _meta(**fields: object) -> ContractMetadata:
    return ContractMetadata(**fields)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- winning


def test_the_higher_precedence_provider_wins_a_conflict() -> None:
    cko = _cko(
        _contribution("template", metadata=_meta(payment_terms_days=30)),
        _contribution("playbook", metadata=_meta(payment_terms_days=45)),
    )
    assert cko.metadata.payment_terms_days == 45


def test_precedence_wins_regardless_of_input_order() -> None:
    """The caller ordering its input wrong must not change the outcome.

    `aggregate` sorts by precedence rather than trusting the order it was handed, so a bug
    in the gather step cannot flip which source wins a conflict.
    """
    forward = _cko(
        _contribution("playbook", metadata=_meta(payment_terms_days=45)),
        _contribution("template", metadata=_meta(payment_terms_days=30)),
    )
    reverse = _cko(
        _contribution("template", metadata=_meta(payment_terms_days=30)),
        _contribution("playbook", metadata=_meta(payment_terms_days=45)),
    )
    assert forward.metadata.payment_terms_days == reverse.metadata.payment_terms_days == 45


def test_a_lower_provider_fills_a_field_the_higher_one_left_empty() -> None:
    """Not every field is a contest. The playbook sets the payment term; the llm supplies
    the language it never mentioned."""
    cko = _cko(
        _contribution("playbook", metadata=_meta(payment_terms_days=45)),
        _contribution("llm", metadata=_meta(payment_terms_days=45, language="en", country="IN")),
    )
    assert cko.metadata.payment_terms_days == 45
    assert cko.metadata.language == "en"
    assert cko.metadata.country == "IN"


# ------------------------------------------------------------------------- recording


def test_the_loser_is_recorded_with_both_values_and_both_provenances() -> None:
    cko = _cko(
        _contribution("template", metadata=_meta(payment_terms_days=30)),
        _contribution("playbook", metadata=_meta(payment_terms_days=45)),
    )

    assert len(cko.conflicts) == 1
    conflict = cko.conflicts[0]
    assert conflict.field == "metadata.payment_terms_days"
    assert conflict.winning_value == "45"
    assert conflict.winning_provenance.provider == "playbook"
    assert conflict.losing_value == "30"
    assert conflict.losing_provenance.provider == "template"
    assert conflict.applied_precedence == "playbook > template"


def test_agreement_is_not_a_conflict() -> None:
    """Two providers supplying the same value is not a disagreement to record."""
    cko = _cko(
        _contribution("playbook", metadata=_meta(payment_terms_days=45)),
        _contribution("template", metadata=_meta(payment_terms_days=45)),
    )
    assert cko.conflicts == ()


def test_a_missing_value_is_not_a_conflict() -> None:
    cko = _cko(
        _contribution("playbook", metadata=_meta(payment_terms_days=45)),
        _contribution("template", metadata=_meta(jurisdiction="IN")),
    )
    assert cko.conflicts == ()
    assert cko.metadata.payment_terms_days == 45
    assert cko.metadata.jurisdiction == "IN"


def test_several_conflicting_fields_are_each_recorded() -> None:
    cko = _cko(
        _contribution("template", metadata=_meta(payment_terms_days=30, governing_law="England")),
        _contribution("playbook", metadata=_meta(payment_terms_days=45, governing_law="India")),
    )
    fields = {c.field for c in cko.conflicts}
    assert fields == {"metadata.payment_terms_days", "metadata.governing_law"}


# ------------------------------------------------------------------- lists are additive


def test_clause_candidates_from_several_providers_are_all_kept() -> None:
    """Lists concatenate; precedence only orders them.

    Two providers each finding clauses both contribute — a conflict resolution here would
    silently drop half the contract's clauses.
    """
    cko = _cko(
        _contribution(
            "clause_library", clause_candidates=(ClauseCandidate(category="confidentiality"),)
        ),
        _contribution("template", clause_candidates=(ClauseCandidate(category="termination"),)),
    )
    categories = [c.category for c in cko.clause_candidates]
    assert set(categories) == {"confidentiality", "termination"}


def test_list_items_come_in_precedence_order() -> None:
    """Higher-authority items first, which is what a downstream ranker expects."""
    cko = _cko(
        _contribution("template", clause_candidates=(ClauseCandidate(category="termination"),)),
        _contribution(
            "clause_library", clause_candidates=(ClauseCandidate(category="confidentiality"),)
        ),
    )
    assert (
        cko.clause_candidates[0].category == "confidentiality"
    )  # clause_library outranks template


def test_playbook_requirements_survive_aggregation() -> None:
    cko = _cko(
        _contribution(
            "playbook",
            requirements=(
                PlaybookRequirement(rule_id="dpdp", kind="require_section", target="DPDP"),
            ),
        )
    )
    assert cko.playbook_rules[0].target == "DPDP"
    assert cko.blocking_requirements[0].rule_id == "dpdp"


# ------------------------------------------------------------------------ the CKO shape


def test_parties_come_from_intent_not_from_providers() -> None:
    """A provider inventing a party would be inventing a fact about the deal."""
    intent = IntentObject(
        contract_type="nda",
        confidence=0.9,
        parties=(Party(name="ProcBay", role="Disclosing Party"),),
    )
    cko = aggregate((_contribution("llm"),), intent, RESOLUTION, contract_id=CONTRACT_ID)
    assert cko.parties[0].name == "ProcBay"


def test_overall_confidence_is_the_minimum_not_the_mean() -> None:
    """A CKO is only as trustworthy as its least trustworthy component. Averaging lets a
    confident metadata extraction paper over a shaky classification."""
    cko = _cko(
        _contribution("playbook", confidence=0.9),
        _contribution("llm", confidence=0.4),
    )
    assert cko.confidence.overall == 0.4
    assert dict(cko.confidence.components)["llm"] == 0.4


def test_formatting_is_taken_from_the_highest_provider_that_has_it() -> None:
    from backend.schemas.template import BlockFingerprint, FormattingManifest

    manifest = FormattingManifest(
        blocks=(BlockFingerprint(index=0, kind="paragraph", text_sha="a"),)
    )
    cko = _cko(
        _contribution("template", formatting=manifest),
        _contribution("llm"),
    )
    assert cko.formatting is not None
    assert len(cko.formatting.blocks) == 1


def test_an_empty_aggregation_is_still_a_valid_cko() -> None:
    """The floor case: llm only, nothing found. A valid, if thin, CKO."""
    cko = _cko(_contribution("llm"))
    assert cko.contract_id == CONTRACT_ID
    assert cko.conflicts == ()
    assert cko.confidence.overall == 1.0


def test_the_cko_carries_its_schema_version() -> None:
    from backend.schemas.cko import CKO_SCHEMA_VERSION

    assert _cko(_contribution("llm")).schema_version == CKO_SCHEMA_VERSION


def test_aggregate_reads_and_writes_nothing() -> None:
    """A pure function, which is what makes it testable as a table.

    No session_factory is passed, and none is needed — the proof is simply that every test
    in this file constructs a CKO without a database.
    """
    with pytest.raises(TypeError):
        aggregate((), INTENT, RESOLUTION)  # type: ignore[call-arg]  # contract_id is required
