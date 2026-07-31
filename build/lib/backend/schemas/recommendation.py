"""Clause recommendations: the chosen clause, the rejected alternatives, and why.

The rejected alternatives are the point. "Why this indemnity clause?" is answered by reading
this artifact — the runners-up are retained with their scores, so the choice is auditable
rather than merely asserted. A recommendation that recorded only the winner would be a
decision nobody could review.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from backend.schemas.cko import ClauseCandidate

__all__ = ["ClauseRecommendation", "ClauseRecommendationSet", "RankedAlternative"]


class RankedAlternative(BaseModel):
    """One candidate that was considered, with its score and why it scored so."""

    model_config = ConfigDict(frozen=True)

    candidate: ClauseCandidate
    score: int
    reasons: tuple[str, ...] = ()


class ClauseRecommendation(BaseModel):
    """For one clause slot: what was chosen, and what was not.

    `alternatives` are the ranked runners-up, chosen excluded. `rationale` is the model's
    one-line justification for taking the top-ranked candidate — or, when it overrode the
    ranking, why. The ranking is arithmetic; the override is a judgement, and a judgement
    must be explained.
    """

    model_config = ConfigDict(frozen=True)

    category: str
    chosen: ClauseCandidate
    chosen_score: int
    alternatives: tuple[RankedAlternative, ...] = ()
    rationale: str = ""


class ClauseRecommendationSet(BaseModel):
    """Every slot's recommendation, wrapped so it can be an artifact with a version."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    recommendations: tuple[ClauseRecommendation, ...] = ()
