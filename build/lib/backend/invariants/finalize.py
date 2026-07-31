"""Selecting the draft to finalize. The choke point's decision, isolated and pure.

Three rules, each of which is a real bug if you get it wrong:

**Not the last draft.** Scores oscillate: attempt 3 may be worse than attempt 2. "Return the
most recent one" is a silent regression that no prompt reliably prevents, so it is decided in
code.

**Not simply the highest score.** A *clean* draft scoring 85 must beat a *blocked* draft
scoring 88. Passing the deterministic gates is a precondition of eligibility, not a
tiebreaker. A contract missing its duration clause is not "nearly right".

**Passing the gates is not the same as passing the pass mark.** A draft with no blockers is
finalizable at any score. If it scores below the pass mark it is finalized anyway and flagged
`needs_human_review` — the system never returns nothing, and never pretends.

Every candidate is re-validated here. The stored `passed` flag from the judge is not trusted:
it was written by a different code path at a different time, and this is the last gate before
a document exists.

Imports neither `agents` nor `openai`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from backend.invariants.validate import score_draft, validate_draft
from backend.schemas.clause import RenderedClause
from backend.schemas.draft import Finding, ValidationReport

__all__ = ["Candidate", "Rejected", "Selected", "select_finalizable"]


@dataclass(frozen=True)
class Candidate:
    """One drafting attempt, as recorded in the append-only ledger."""

    attempt: int
    path: str
    markdown: str
    #: The judge's composite score, or None if the judge never ran on this attempt.
    score: int | None = None


@dataclass(frozen=True)
class Selected:
    candidate: Candidate
    report: ValidationReport
    score: int
    needs_human_review: bool


@dataclass(frozen=True)
class Rejected:
    """No attempt passed the gates. There is no document, and there will not be one."""

    findings: tuple[Finding, ...]
    best_attempt: int | None
    hint: str


def _score_of(candidate: Candidate, report: ValidationReport) -> int:
    """The judge's score if it ran; otherwise the gates alone, awarding no prose points.

    Conservative on purpose: an unjudged draft is never preferred to a judged one on the
    strength of points nobody awarded it.
    """
    if candidate.score is not None:
        return candidate.score
    return score_draft(report, judge_points=0)


def select_finalizable(
    candidates: Sequence[Candidate],
    expected: Sequence[RenderedClause],
    required_ids: frozenset[str] | set[str],
    *,
    pass_score: int,
) -> Selected | Rejected:
    if not candidates:
        return Rejected((), None, "There is no draft yet. Call run_drafting_agent.")

    graded = [(c, validate_draft(c.markdown, expected, required_ids)) for c in candidates]
    eligible = [(c, r) for c, r in graded if r.ok]

    if not eligible:
        # Report the defects of the *best* failing draft: it is the one worth fixing.
        best, report = max(graded, key=lambda pair: (_score_of(*pair), -pair[0].attempt))
        return Rejected(
            findings=report.blockers,
            best_attempt=best.attempt,
            hint=(
                f"No attempt passed the gates. Attempt {best.attempt} came closest with "
                f"{len(report.blockers)} blocker(s). Fix them and draft again; a contract "
                "cannot be produced while any blocker stands."
            ),
        )

    # Highest score among the eligible; lowest attempt breaks a tie, so the choice is stable.
    candidate, report = max(eligible, key=lambda pair: (_score_of(*pair), -pair[0].attempt))
    score = _score_of(candidate, report)

    return Selected(
        candidate=candidate,
        report=report,
        score=score,
        needs_human_review=score < pass_score,
    )
