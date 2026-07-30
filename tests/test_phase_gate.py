"""The CKO must be usable before Phase B spends anything on it.

The signature test proves nothing else was passed to the drafting engine. This proves what
*was* passed is enough to draft from. Both are needed: a CKO that satisfies the signature
but lacks a formatting manifest passes into template mode and silently regenerates the
document the user uploaded specifically to preserve.
"""

from __future__ import annotations

import uuid

import pytest

from backend.invariants.phase_gate import PhaseGateError, assert_cko_complete, missing_requirements
from backend.schemas.cko import ClauseCandidate, ContractKnowledgeObject
from backend.schemas.intent import IntentObject, ResolutionPlan
from backend.schemas.template import BlockFingerprint, FormattingManifest


def _cko(**overrides: object) -> ContractKnowledgeObject:
    base: dict[str, object] = {
        "contract_id": uuid.uuid4(),
        "resolution": ResolutionPlan(providers=("llm",)),
        "intent": IntentObject(contract_type="nda", confidence=0.9),
    }
    base.update(overrides)
    return ContractKnowledgeObject(**base)  # type: ignore[arg-type]


def _formatting() -> FormattingManifest:
    return FormattingManifest(blocks=(BlockFingerprint(index=0, kind="paragraph", text_sha="a"),))


def test_a_usable_cko_passes() -> None:
    assert missing_requirements(_cko()) == ()
    assert_cko_complete(_cko())


def test_unanswered_questions_block_drafting() -> None:
    """Clarification is cheap before aggregation and expensive after drafting has begun.

    A question answered at intent time invalidates one artifact; the same question at
    drafting time invalidates six.
    """
    cko = _cko(
        intent=IntentObject(
            contract_type="nda",
            confidence=0.4,
            needs_clarification=("Who are the parties?",),
        )
    )

    with pytest.raises(PhaseGateError, match="Who are the parties"):
        assert_cko_complete(cko)


def test_template_mode_without_a_formatting_manifest_is_refused() -> None:
    """The failure this gate exists for.

    It satisfies the signature test and the import guard, and then produces a regenerated
    document — losing exactly the formatting the upload was meant to preserve.
    """
    cko = _cko(intent=IntentObject(contract_type="sla", confidence=0.9, mode="template"))

    with pytest.raises(PhaseGateError, match="regenerate the document"):
        assert_cko_complete(cko)


def test_template_mode_with_an_empty_manifest_is_also_refused() -> None:
    """Present-but-empty is the same failure wearing a different shape."""
    cko = _cko(
        intent=IntentObject(contract_type="sla", confidence=0.9, mode="template"),
        formatting=FormattingManifest(),
    )

    with pytest.raises(PhaseGateError, match="no section can be preserved"):
        assert_cko_complete(cko)


def test_template_mode_with_a_manifest_passes() -> None:
    cko = _cko(
        intent=IntentObject(contract_type="sla", confidence=0.9, mode="template"),
        formatting=_formatting(),
    )
    assert_cko_complete(cko)


def test_library_mode_without_clause_candidates_is_refused() -> None:
    cko = _cko(intent=IntentObject(contract_type="nda", confidence=0.9, mode="library_playbook"))

    with pytest.raises(PhaseGateError, match="no clause candidates"):
        assert_cko_complete(cko)


def test_library_mode_with_candidates_passes() -> None:
    cko = _cko(
        intent=IntentObject(contract_type="nda", confidence=0.9, mode="library_playbook"),
        clause_candidates=(ClauseCandidate(category="Confidentiality"),),
    )
    assert_cko_complete(cko)


def test_a_cko_with_no_providers_is_refused() -> None:
    cko = _cko(resolution=ResolutionPlan(providers=()))

    with pytest.raises(PhaseGateError, match="nothing to draft from"):
        assert_cko_complete(cko)


def test_every_reason_is_reported_not_just_the_first() -> None:
    """A gate that reports one problem at a time turns one round trip into three."""
    cko = _cko(
        resolution=ResolutionPlan(providers=()),
        intent=IntentObject(
            contract_type="sla",
            confidence=0.2,
            mode="template",
            needs_clarification=("Which SLA?",),
        ),
    )

    assert len(missing_requirements(cko)) == 3
