"""The clause taxonomy is a fixed, shared vocabulary, and conformance to it is checked.

`ClauseCandidate.category` is the field every future capability joins on. If it could be
free text, two contracts with a confidentiality clause might call it two different things,
and clause recommendation, risk roll-ups and playbook checks would all silently miss one of
them. So the vocabulary is fixed and conformance is a gate, not a request.
"""

from __future__ import annotations

from backend.invariants.taxonomy import (
    categories,
    describe,
    is_known,
    taxonomy_prompt_block,
    unknown_categories,
)
from backend.schemas.cko import ClauseCandidate


def test_the_taxonomy_loads_and_is_non_trivial() -> None:
    cats = categories()
    assert len(cats) > 30
    assert len(cats) == len(set(cats)), "ids must be unique"


def test_the_load_bearing_categories_exist() -> None:
    """Named individually because a rename is a breaking change for every consumer."""
    for category in (
        "confidentiality",
        "indemnity",
        "limitation_of_liability",
        "termination",
        "governing_law",
        "data_protection",
        "intellectual_property",
        "other",
    ):
        assert is_known(category), category


def test_an_invented_category_is_not_known() -> None:
    assert not is_known("confidential_information")  # the classic near-miss
    assert not is_known("ip")
    assert not is_known("")


def test_unknown_categories_finds_near_misses() -> None:
    candidates = (
        ClauseCandidate(category="confidentiality"),
        ClauseCandidate(category="confidential_information"),  # near miss
        ClauseCandidate(category="indemnity"),
        ClauseCandidate(category="indemnification"),  # near miss
    )

    assert unknown_categories(candidates) == ("confidential_information", "indemnification")


def test_unknown_categories_deduplicates() -> None:
    """A hundred clauses in one bad category is one problem, not a hundred."""
    candidates = tuple(ClauseCandidate(category="ip") for _ in range(100))
    assert unknown_categories(candidates) == ("ip",)


def test_a_conforming_set_has_no_unknowns() -> None:
    candidates = (
        ClauseCandidate(category="termination"),
        ClauseCandidate(category="fees_and_payment"),
        ClauseCandidate(category="other"),
    )
    assert unknown_categories(candidates) == ()


def test_categories_have_descriptions() -> None:
    assert describe("indemnity")
    assert describe("not_a_real_category") == ""


def test_the_prompt_block_covers_every_category() -> None:
    """The list a model is shown is built from the file, so it cannot drift from the list
    it is validated against."""
    block = taxonomy_prompt_block()
    for category in categories():
        assert f"`{category}`" in block


def test_the_prompt_block_is_stable_across_calls() -> None:
    """Cached load; a per-call reshuffle would change every classification prompt's hash."""
    assert taxonomy_prompt_block() == taxonomy_prompt_block()
