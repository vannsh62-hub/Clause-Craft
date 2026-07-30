"""Reference documents are analysed, never copied.

The primary defence is structural — `KnowledgeGraph` has no field that can hold clause
text, and Phase B never receives raw reference documents — so this gate exists to prove the
structural defence held rather than to be the only thing standing in the way.
"""

from __future__ import annotations

from backend.invariants.leakage import NGRAM_WORDS, find_leaks, ngrams

REFERENCE = (
    "The Receiving Party shall not disclose the Confidential Information to any "
    "third party without the prior written consent of the Disclosing Party, and shall "
    "return all materials upon termination of this Agreement."
)


def test_a_clean_draft_reports_nothing() -> None:
    draft = "Each party will keep the other's information private and return it when asked."
    assert find_leaks(draft, [("vendor-nda.docx", REFERENCE)]) == ()


def test_a_copied_passage_is_caught() -> None:
    draft = (
        "1. Confidentiality\n\n"
        "The Receiving Party shall not disclose the Confidential Information to any "
        "third party without the prior written consent of the Disclosing Party.\n"
    )

    hits = find_leaks(draft, [("vendor-nda.docx", REFERENCE)])

    assert hits
    assert hits[0].document == "vendor-nda.docx"
    assert "receiving party shall not disclose" in hits[0].passage


def test_a_distinctive_token_cannot_survive_into_the_output() -> None:
    """The canary the reference-leakage milestone is built around."""
    reference = "The parties agree that the ZORBLAX QUUX 7719 protocol governs all disputes here."
    draft = "The parties agree that the ZORBLAX QUUX 7719 protocol governs all disputes here."

    assert find_leaks(draft, [("ref.docx", reference)])


def test_reflowing_and_recasing_is_still_a_copy() -> None:
    """Whitespace and capitalisation are not a defence.

    Normalisation matches the fidelity gate's, so the two agree on what "the same text"
    means.
    """
    draft = REFERENCE.upper().replace(" ", "\n   ")
    assert find_leaks(draft, [("ref.docx", REFERENCE)])


def test_ordinary_legal_phrasing_does_not_trip_the_gate() -> None:
    """A gate that fires on boilerplate is a gate reviewers learn to ignore.

    Two contracts that share nothing but the language every contract shares must not
    produce a finding.
    """
    reference = "This Agreement shall be governed by the laws of India."
    draft = "This Agreement shall be governed by the laws of Singapore."

    assert find_leaks(draft, [("ref.docx", reference)]) == ()


def test_one_copied_passage_produces_one_finding_not_a_dozen() -> None:
    """Overlapping shards are collapsed.

    A copied sentence yields many near-identical n-grams; reporting each separately makes
    a findings list nobody reads.
    """
    hits = find_leaks(REFERENCE, [("ref.docx", REFERENCE)])
    assert len(hits) == 1
    assert len(hits[0].passage.split()) > NGRAM_WORDS


def test_short_text_cannot_produce_shards() -> None:
    assert ngrams("too short") == set()
    assert find_leaks("too short", [("ref.docx", REFERENCE)]) == ()


def test_each_reference_is_attributed_separately() -> None:
    other = "Payment shall be made within forty five days of receipt of a valid invoice."
    draft = f"{REFERENCE}\n\n{other}"

    documents = {
        hit.document for hit in find_leaks(draft, [("a.docx", REFERENCE), ("b.docx", other)])
    }
    assert documents == {"a.docx", "b.docx"}
