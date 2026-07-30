"""Clause recommendation: retrieve → rank → recommend.

The CKO already carries the retrieved candidates (the clause-library provider put them
there). This stage does the other two steps: rank the candidates competing for each slot,
choose among them, and record the choice with its rejected alternatives.

## Why the ranking is code and the choice is a model

Ranking is arithmetic over structured fields — jurisdiction fit, mandatory, risk — and
arithmetic must be reproducible, so it is `invariants/ranking.py`, a pure function. Choosing
is a judgement: usually the top-ranked candidate, but a run might have reason to prefer a
runner-up, and that reason must be recorded. So the ranking is deterministic and the
selection is explained.

For a library with one clause per slot — the common case today — there is nothing to choose
between and the recommendation simply confirms the single candidate, still recording that it
was the only one. The machinery earns its keep when a slot has genuine alternatives.

## The artifact is the audit trail

`07-clause-recommendations.json` retains every runner-up with its score. "Why this
indemnity?" is answered by reading it — the alternatives that lost, and by how much.

Phase B, so no reaching back: this reads the CKO's `clause_candidates` and ranks them. It
does not re-open the clause library — the candidates it needs are already in the object it
was handed.
"""

from __future__ import annotations

from collections import defaultdict

from backend.artifacts import Artifact, ArtifactStore
from backend.core.logging import get_logger
from backend.core.run_context import RunContext
from backend.invariants.ranking import RankCriteria, rank_candidates
from backend.schemas.cko import ClauseCandidate, ContractKnowledgeObject
from backend.schemas.recommendation import (
    ClauseRecommendation,
    ClauseRecommendationSet,
    RankedAlternative,
)

__all__ = ["recommend_clauses"]

log = get_logger(__name__)


def _criteria(cko: ContractKnowledgeObject) -> RankCriteria:
    return RankCriteria(
        jurisdiction=cko.intent.jurisdiction,
        industry=cko.intent.industry,
        prefer_low_risk=True,
    )


def _recommend_slot(
    category: str,
    candidates: list[ClauseCandidate],
    criteria: RankCriteria,
) -> ClauseRecommendation:
    ranked = rank_candidates(candidates, criteria)
    winner = ranked[0]
    alternatives = tuple(
        RankedAlternative(candidate=r.candidate, score=r.score, reasons=r.reasons)
        for r in ranked[1:]
    )
    rationale = (
        f"top-ranked of {len(ranked)} candidate(s)"
        if len(ranked) > 1
        else "the only approved clause for this slot"
    )
    return ClauseRecommendation(
        category=category,
        chosen=winner.candidate,
        chosen_score=winner.score,
        alternatives=alternatives,
        rationale=rationale,
    )


async def recommend_clauses(
    cko: ContractKnowledgeObject, ctx: RunContext
) -> ClauseRecommendationSet:
    """Rank and choose a clause for each slot, and persist `07-clause-recommendations.json`.

    Groups the CKO's candidates by category, ranks within each group, and recommends the
    top — recording the runners-up. A category with no candidates produces no
    recommendation rather than an empty one; there is nothing to choose.
    """
    by_category: dict[str, list[ClauseCandidate]] = defaultdict(list)
    for candidate in cko.clause_candidates:
        by_category[candidate.category].append(candidate)

    criteria = _criteria(cko)
    recommendations = tuple(
        _recommend_slot(category, candidates, criteria)
        for category, candidates in sorted(by_category.items())
    )

    result = ClauseRecommendationSet(recommendations=recommendations)
    await ArtifactStore(ctx.session_factory, ctx.contract_id).save(
        Artifact.CLAUSE_RECOMMENDATIONS, result
    )
    log.info("recommended %d clause slot(s)", len(recommendations))
    return result
