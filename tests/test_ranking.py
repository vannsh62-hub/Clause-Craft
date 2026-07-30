"""Clause ranking is a pure, deterministic function.

Enterprise libraries return several plausible clauses for one slot, and choosing among them
must be reproducible: the same candidates and criteria must always rank the same way, or
"why was this indemnity chosen?" has no stable answer. So ranking is arithmetic over
structured fields, tested as a table — no model, no database.
"""

from __future__ import annotations

from backend.invariants.ranking import RankCriteria, rank_candidates
from backend.schemas.cko import ClauseCandidate, SourceRef


def _candidate(
    clause_id: str,
    *,
    category: str = "indemnity",
    applicability: tuple[str, ...] = (),
    risk: str = "medium",
    mandatory: bool = False,
    confidence: float = 0.5,
) -> ClauseCandidate:
    return ClauseCandidate(
        category=category,
        applicability=applicability,
        risk=risk,  # type: ignore[arg-type]
        mandatory=mandatory,
        confidence=confidence,
        source_ref=SourceRef(provider="clause_library", clause_id=clause_id),
    )


# --------------------------------------------------------------------------- determinism


def test_the_same_input_ranks_the_same_way_every_time() -> None:
    """The property the whole design rests on."""
    candidates = [
        _candidate("a", applicability=("IN",), mandatory=True),
        _candidate("b", risk="low"),
        _candidate("c", applicability=("software",)),
    ]
    criteria = RankCriteria(jurisdiction="IN", industry="software")

    first = rank_candidates(candidates, criteria)
    second = rank_candidates(candidates, criteria)

    assert [r.candidate.source_ref.clause_id for r in first] == [
        r.candidate.source_ref.clause_id for r in second
    ]


def test_input_order_does_not_change_the_ranking() -> None:
    """Ranking must depend on the candidates, not the order they arrived in."""
    a = _candidate("a", applicability=("IN",), mandatory=True)
    b = _candidate("b", risk="low")

    forward = rank_candidates([a, b], RankCriteria(jurisdiction="IN"))
    reverse = rank_candidates([b, a], RankCriteria(jurisdiction="IN"))

    assert [r.candidate.source_ref.clause_id for r in forward] == [
        r.candidate.source_ref.clause_id for r in reverse
    ]


def test_equal_scores_break_ties_by_clause_id_not_by_luck() -> None:
    """Two clauses that score the same must order deterministically, or the recommendation
    artifact shows spurious changes between identical runs."""
    a = _candidate("zzz", risk="low")
    b = _candidate("aaa", risk="low")

    ranked = rank_candidates([a, b], RankCriteria())

    assert [r.candidate.source_ref.clause_id for r in ranked] == ["aaa", "zzz"]


# --------------------------------------------------------------------------- scoring


def test_a_jurisdiction_match_outscores_a_non_match() -> None:
    match = _candidate("match", applicability=("IN",))
    other = _candidate("other", applicability=("US",))

    ranked = rank_candidates([other, match], RankCriteria(jurisdiction="IN"))

    assert ranked[0].candidate.source_ref.clause_id == "match"


def test_a_mandatory_clause_outscores_an_optional_one_all_else_equal() -> None:
    mand = _candidate("mand", mandatory=True)
    opt = _candidate("opt", mandatory=False)

    ranked = rank_candidates([opt, mand], RankCriteria())

    assert ranked[0].candidate.source_ref.clause_id == "mand"


def test_a_prior_choice_earns_points() -> None:
    """Spec 02 memory: a clause chosen before is evidence, not a mandate."""
    chosen_before = _candidate("nda.confidentiality")
    fresh = _candidate("nda.other")

    ranked = rank_candidates(
        [fresh, chosen_before],
        RankCriteria(prior_choices=("nda.confidentiality",)),
    )

    assert ranked[0].candidate.source_ref.clause_id == "nda.confidentiality"


def test_low_risk_is_preferred_by_default() -> None:
    low = _candidate("low", risk="low")
    high = _candidate("high", risk="high")

    ranked = rank_candidates([high, low], RankCriteria(prefer_low_risk=True))

    assert ranked[0].candidate.source_ref.clause_id == "low"


def test_an_absent_criterion_penalises_nobody() -> None:
    """An unstated industry should not push every clause down — it is simply not a factor."""
    a = _candidate("a", applicability=("software",))
    b = _candidate("b", applicability=("healthcare",))

    ranked = rank_candidates([a, b], RankCriteria())  # no industry

    assert {r.score for r in ranked} == {0}, "nothing scored, nothing penalised"


# --------------------------------------------------------------------------- reasons


def test_every_point_names_its_reason() -> None:
    """The ranking is not a black box: the score is a sum of named contributions."""
    candidate = _candidate(
        "c", applicability=("IN", "software"), mandatory=True, risk="low", confidence=0.9
    )

    ranked = rank_candidates([candidate], RankCriteria(jurisdiction="IN", industry="software"))

    reasons = " ".join(ranked[0].reasons)
    assert "IN" in reasons
    assert "software" in reasons
    assert "mandatory" in reasons
    assert ranked[0].score == sum(int(r.split()[0][1:]) for r in ranked[0].reasons)


def test_an_empty_candidate_set_ranks_to_nothing() -> None:
    assert rank_candidates([], RankCriteria()) == ()
