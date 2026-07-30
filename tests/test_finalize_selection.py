"""Selection rules for the choke point. Pure — no database, no model, no tokens."""

from __future__ import annotations

import pytest

from backend.clauselib.loader import clauses_for, required_clause_ids
from backend.invariants.finalize import Candidate, Rejected, Selected, select_finalizable
from backend.invariants.render import render_clause
from backend.schemas.clause import RenderedClause

NDA_VARS = {
    "disclosing_party": "ABC Pvt Ltd",
    "receiving_party": "XYZ Pvt Ltd",
    "duration_years": "3",
    "effective_date": "1 August 2026",
    "term_end_date": "1 August 2029",
    "governing_law_country": "India",
    "jurisdiction_city": "Mumbai",
    "disclosing_signatory": "Jane Rao",
    "receiving_signatory": "Sam Patel",
}
REQUIRED = required_clause_ids("nda")
PASS = 90


def _rendered() -> list[RenderedClause]:
    return [render_clause(c, {k: NDA_VARS[k] for k in c.variables}) for c in clauses_for("nda")]


def _clean(drop: str | None = None) -> str:
    return "\n\n".join(f"## {c.title}\n\n{c.text}" for c in _rendered() if c.clause_id != drop)


def _select(*candidates: Candidate) -> Selected | Rejected:
    return select_finalizable(candidates, _rendered(), REQUIRED, pass_score=PASS)


# --------------------------------------------------------------------- the three rules


def test_the_best_passing_draft_wins_not_the_last_one() -> None:
    """Scores oscillate. `85, 88, 84` must yield attempt 2, not attempt 3."""
    outcome = _select(
        Candidate(1, "draft_v1.md", _clean(), 85),
        Candidate(2, "draft_v2.md", _clean(), 88),
        Candidate(3, "draft_v3.md", _clean(), 84),
    )

    assert isinstance(outcome, Selected)
    assert outcome.candidate.attempt == 2
    assert outcome.score == 88
    assert outcome.needs_human_review is True  # 88 < 90


def test_a_clean_eighty_five_beats_a_blocked_eighty_eight() -> None:
    """Passing the gates is a precondition of eligibility, not a tiebreaker. A contract
    missing its duration clause is not 'nearly right'."""
    outcome = _select(
        Candidate(1, "draft_v1.md", _clean(), 85),
        Candidate(2, "draft_v2.md", _clean(drop="nda.duration"), 88),
    )

    assert isinstance(outcome, Selected)
    assert outcome.candidate.attempt == 1
    assert outcome.score == 85


def test_a_draft_that_passes_the_gates_but_not_the_pass_mark_is_finalized_and_flagged() -> None:
    """The system never returns nothing, and never pretends."""
    outcome = _select(Candidate(1, "draft_v1.md", _clean(), 72))

    assert isinstance(outcome, Selected)
    assert outcome.needs_human_review is True


def test_a_passing_draft_above_the_pass_mark_needs_no_review() -> None:
    outcome = _select(Candidate(1, "draft_v1.md", _clean(), 95))

    assert isinstance(outcome, Selected)
    assert outcome.needs_human_review is False


# ------------------------------------------------------------------------- refusal


def test_no_passing_draft_produces_no_document() -> None:
    outcome = _select(
        Candidate(1, "draft_v1.md", _clean(drop="nda.duration"), 80),
        Candidate(2, "draft_v2.md", _clean(drop="nda.governing_law"), 88),
    )

    assert isinstance(outcome, Rejected)
    assert outcome.best_attempt == 2, "report the defects of the draft worth fixing"
    assert any(f.clause_id == "nda.governing_law" for f in outcome.findings)
    assert "cannot be produced while any blocker stands" in outcome.hint


def test_a_placeholder_blocks_finalization_however_high_the_score() -> None:
    outcome = _select(Candidate(1, "draft_v1.md", _clean() + "\n\nTerm: TBD", 99))
    assert isinstance(outcome, Rejected)


def test_reworded_approved_text_blocks_finalization() -> None:
    tampered = _clean().replace("strict confidence", "reasonable confidence")
    outcome = _select(Candidate(1, "draft_v1.md", tampered, 99))

    assert isinstance(outcome, Rejected)
    assert any(f.dimension == "fidelity" for f in outcome.findings)


def test_no_candidates_at_all() -> None:
    outcome = select_finalizable([], _rendered(), REQUIRED, pass_score=PASS)
    assert isinstance(outcome, Rejected)
    assert "no draft yet" in outcome.hint


# ------------------------------------------------------- the stored verdict is not trusted


def test_a_draft_the_judge_marked_passing_is_still_re_validated() -> None:
    """`passed` was written by a different code path at a different time. This is the last
    gate before a document exists."""
    outcome = _select(Candidate(1, "draft_v1.md", _clean(drop="nda.confidentiality"), 100))

    assert isinstance(outcome, Rejected), "a stored score of 100 must not buy a pass"


def test_an_unjudged_draft_is_scored_conservatively() -> None:
    """`score=None` means the judge never ran. It is scored on the gates alone, awarding no
    prose points, so it never outranks a judged draft on points nobody gave it."""
    outcome = _select(
        Candidate(1, "draft_v1.md", _clean(), None),
        Candidate(2, "draft_v2.md", _clean(), 75),
    )

    assert isinstance(outcome, Selected)
    assert outcome.candidate.attempt == 2, "the judged 75 beats an unjudged gates-only 70"


def test_an_unjudged_clean_draft_is_still_finalizable_when_it_is_all_there_is() -> None:
    outcome = _select(Candidate(1, "draft_v1.md", _clean(), None))

    assert isinstance(outcome, Selected)
    assert outcome.score == 70  # gates only
    assert outcome.needs_human_review is True


# ------------------------------------------------------------------------ determinism


def test_a_tie_is_broken_by_the_earliest_attempt() -> None:
    outcome = _select(
        Candidate(1, "draft_v1.md", _clean(), 91),
        Candidate(2, "draft_v2.md", _clean(), 91),
    )

    assert isinstance(outcome, Selected)
    assert outcome.candidate.attempt == 1


def test_selection_is_deterministic() -> None:
    candidates = (
        Candidate(1, "draft_v1.md", _clean(), 85),
        Candidate(2, "draft_v2.md", _clean(), 88),
        Candidate(3, "draft_v3.md", _clean(drop="nda.duration"), 99),
    )
    first = _select(*candidates)
    for _ in range(10):
        again = _select(*candidates)
        assert isinstance(first, Selected) and isinstance(again, Selected)
        assert again.candidate.attempt == first.candidate.attempt
        assert again.score == first.score


@pytest.mark.parametrize("pass_score", [0, 50, 90, 100])
def test_the_pass_mark_only_controls_the_review_flag_not_eligibility(pass_score: int) -> None:
    outcome = select_finalizable(
        [Candidate(1, "draft_v1.md", _clean(), 85)], _rendered(), REQUIRED, pass_score=pass_score
    )

    assert isinstance(outcome, Selected), "a clean draft is always finalizable"
    assert outcome.needs_human_review is (pass_score > 85)
