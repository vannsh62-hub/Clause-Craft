"""Ranking clause candidates — deterministic, no model.

Enterprise clause libraries return several plausible clauses for one slot: three payment
clauses, two indemnities. Choosing among them is a judgement that must be *recorded*, and it
must be *reproducible* — the same candidates and the same criteria must always rank the same
way, or "why was this indemnity chosen?" has no stable answer.

So ranking is a pure scoring function over structured fields, not a model call. The model's
job (M12's recommendation agent) is to choose among ranked candidates and explain the
choice; deciding the *order* is arithmetic, and arithmetic does not belong in a model.

The score is a sum of small, named contributions. Every point a candidate earns names the
reason it earned it, so the ranking is not a black box — the recommendation artifact can
show "chosen: +3 jurisdiction match, +2 mandatory, +1 lower risk" rather than an opaque
number.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from backend.schemas.cko import ClauseCandidate

__all__ = ["RankCriteria", "RankedCandidate", "rank_candidates"]

#: Points, named so the total is explainable. Kept small and legible on purpose — a scoring
#: function nobody can read is one nobody trusts, and it silently rots.
_JURISDICTION_MATCH = 3
_INDUSTRY_MATCH = 2
_MANDATORY = 2
_PRIOR_CHOICE = 2
_LOW_RISK = 1
_CONFIDENCE = 1  # scaled by the candidate's own confidence


class RankCriteria(BaseModel):
    """What the ranking is *for*: the context a good clause fits.

    All optional. A criterion that is absent contributes nothing rather than penalising —
    an unstated industry should not push every clause down, it should simply not be a factor.
    """

    model_config = ConfigDict(frozen=True)

    jurisdiction: str | None = None
    industry: str | None = None
    #: Clause ids chosen for this kind of contract before (spec 02 memory). A prior choice
    #: is evidence, not a mandate — it earns points, it does not force selection.
    prior_choices: tuple[str, ...] = ()
    #: Prefer lower-risk clauses. A conservative default; a negotiation might invert it.
    prefer_low_risk: bool = True


class RankedCandidate(BaseModel):
    """A candidate with its score and the reasons behind it."""

    model_config = ConfigDict(frozen=True)

    candidate: ClauseCandidate
    score: int
    reasons: tuple[str, ...]


def _score(candidate: ClauseCandidate, criteria: RankCriteria) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    applic = {a.casefold() for a in candidate.applicability}

    if criteria.jurisdiction and criteria.jurisdiction.casefold() in applic:
        score += _JURISDICTION_MATCH
        reasons.append(f"+{_JURISDICTION_MATCH} applies in {criteria.jurisdiction}")

    if criteria.industry and criteria.industry.casefold() in applic:
        score += _INDUSTRY_MATCH
        reasons.append(f"+{_INDUSTRY_MATCH} fits the {criteria.industry} industry")

    if candidate.mandatory:
        score += _MANDATORY
        reasons.append(f"+{_MANDATORY} mandatory for this contract type")

    clause_id = candidate.source_ref.clause_id if candidate.source_ref else None
    if clause_id and clause_id in criteria.prior_choices:
        score += _PRIOR_CHOICE
        reasons.append(f"+{_PRIOR_CHOICE} chosen for a contract like this before")

    if criteria.prefer_low_risk and candidate.risk == "low":
        score += _LOW_RISK
        reasons.append(f"+{_LOW_RISK} lower risk")

    if candidate.confidence >= 0.8:
        score += _CONFIDENCE
        reasons.append(f"+{_CONFIDENCE} high classification confidence")

    return score, reasons


def rank_candidates(
    candidates: Sequence[ClauseCandidate],
    criteria: RankCriteria,
) -> tuple[RankedCandidate, ...]:
    """Rank `candidates`, best first.

    Ties break by clause id (then category), so the order is total and stable — two runs
    with the same input produce the same ranking, byte for byte. Without a deterministic
    tiebreak, equal-scoring clauses would reshuffle between runs and the recommendation
    artifact would show spurious changes.
    """

    ranked: list[RankedCandidate] = []
    for candidate in candidates:
        score, reasons = _score(candidate, criteria)
        ranked.append(RankedCandidate(candidate=candidate, score=score, reasons=tuple(reasons)))

    def sort_key(item: RankedCandidate) -> tuple[int, str, str]:
        clause_id = item.candidate.source_ref.clause_id if item.candidate.source_ref else ""
        # Negate score so higher sorts first; ids ascending for a stable tiebreak.
        return (-item.score, clause_id or "", item.candidate.category)

    return tuple(sorted(ranked, key=sort_key))
