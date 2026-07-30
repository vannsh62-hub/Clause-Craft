"""A generated draft must be shaped like a contract.

The defect this pins: drafts that opened `## Scope of Services`, carried no title, no party
block, no clause numbers and no execution page, and stopped after the last clause. Every
assertion here names a structural convention a legal document has and ours did not.
"""

from __future__ import annotations

import uuid

from backend.invariants.docx_build import document_to_text
from backend.phase_b.document import build_contract_document, title_for
from backend.schemas.cko import ContractKnowledgeObject, ContractMetadata
from backend.schemas.intent import IntentObject, Party, ResolutionPlan
from backend.schemas.plan import SectionDecision, TransformationPlan


def _cko(**over: object) -> ContractKnowledgeObject:
    base = {
        "contract_id": uuid.uuid4(),
        "resolution": ResolutionPlan(providers=("llm",)),
        "intent": IntentObject(
            contract_type="service",
            confidence=0.95,
            parties=(
                Party(name="Nova Techset Pvt Ltd", role="Service Provider"),
                Party(name="Katalyst Solutions Pvt Ltd", role="Client"),
            ),
        ),
        "metadata": ContractMetadata(effective_date="2026-08-01"),
    }
    base.update(over)
    return ContractKnowledgeObject(**base)  # type: ignore[arg-type]


_PLAN = TransformationPlan(
    add=(
        SectionDecision(name="Scope of Services", decision="add", reason="core"),
        SectionDecision(name="Fees and Payment", decision="add", reason="core"),
    )
)

_TEXT = {
    "Scope of Services": "The Provider shall provide the Services.",
    "Fees and Payment": "Payment falls due within 30 days.",
}


def _document(**over: object):
    return build_contract_document(_cko(**over), _PLAN, _TEXT, ("WHEREAS the parties agree;",))


def test_the_document_has_a_title_taken_from_the_contract_type() -> None:
    assert title_for(_cko()) == "Services Agreement"
    assert _document().title == "Services Agreement"


def test_the_preamble_names_both_parties_and_their_roles() -> None:
    preamble = _document().preamble

    assert "Nova Techset Pvt Ltd" in preamble
    assert "Katalyst Solutions Pvt Ltd" in preamble
    assert '(the "Service Provider")' in preamble
    assert "2026-08-01" in preamble


def test_an_unknown_effective_date_is_omitted_rather_than_invented() -> None:
    """A fabricated commencement date is worse than an undated draft."""
    doc = _document(metadata=ContractMetadata())

    assert "between" in doc.preamble
    assert " on " not in doc.preamble


def test_every_party_appears_on_the_execution_page() -> None:
    doc = _document()

    assert [s.name for s in doc.signatories] == [
        "Nova Techset Pvt Ltd",
        "Katalyst Solutions Pvt Ltd",
    ]
    assert doc.execution_note.startswith("IN WITNESS WHEREOF")


def test_the_planned_clauses_become_the_numbered_body() -> None:
    doc = _document()

    assert [c.heading for c in doc.clauses] == ["Scope of Services", "Fees and Payment"]
    assert doc.clauses[0].text == "The Provider shall provide the Services."


def test_the_rendered_text_contains_the_whole_skeleton() -> None:
    """The end-to-end shape check: this is what the user actually reads."""
    text = document_to_text(_document())

    assert text.startswith("# Services Agreement")
    assert "Nova Techset Pvt Ltd" in text
    assert "WHEREAS" in text
    assert "1. Scope of Services" in text
    assert "2. Fees and Payment" in text
    assert "IN WITNESS WHEREOF" in text
    assert "By: ______________________________" in text


def test_a_type_with_no_display_name_still_gets_a_readable_title() -> None:
    doc = _cko(intent=IntentObject(contract_type="vendor_agreement", confidence=0.9))
    assert title_for(doc) == "Vendor Agreement"


def test_a_bare_type_name_is_completed_into_a_document_name() -> None:
    """Any type is draftable, so most titles come from the fallback — "Employment" alone is
    a subject, not a document."""
    doc = _cko(intent=IntentObject(contract_type="employment", confidence=0.9))
    assert title_for(doc) == "Employment Agreement"


def test_a_type_that_already_names_a_document_is_left_alone() -> None:
    doc = _cko(intent=IntentObject(contract_type="lease", confidence=0.9))
    assert title_for(doc) == "Lease", "not 'Lease Agreement Agreement'"
