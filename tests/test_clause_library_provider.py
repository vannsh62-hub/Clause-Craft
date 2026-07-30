"""The clause-library provider and clause recommendation — Mode 3.

Spec 01's behaviour, now a provider plus a Phase B recommendation stage. The library
provider retrieves approved clauses as candidates; recommendation ranks and chooses among
them, recording the rejected alternatives.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.artifacts import Artifact, ArtifactStore
from backend.clauselib.loader import clauses_for, required_clause_ids
from backend.core.config import settings
from backend.core.run_context import RunContext
from backend.invariants.taxonomy import is_known
from backend.knowledge.providers.clause_library import ClauseLibraryProvider, clause_to_candidate
from backend.phase_a.aggregator import aggregate
from backend.phase_b.recommendation import recommend_clauses
from backend.schemas.cko import ClauseCandidate, SourceRef
from backend.schemas.intent import IntentObject, ResolutionPlan
from backend.schemas.recommendation import ClauseRecommendationSet
from backend.workspace.models import Contract

INTENT = IntentObject(contract_type="nda", confidence=0.9, jurisdiction="IN")


@pytest_asyncio.fixture
async def ctx() -> AsyncIterator[RunContext]:
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    cid = uuid.uuid4()
    async with factory() as s:
        s.add(Contract(id=cid, contract_type="nda", request="Draft an NDA"))
        await s.commit()
    try:
        yield RunContext(contract_id=cid, session_factory=factory, contract_type="nda")
    finally:
        async with factory() as s:
            await s.execute(delete(Contract).where(Contract.id == cid))
            await s.commit()
        await engine.dispose()


# ------------------------------------------------------------------------- the provider


async def test_it_retrieves_approved_clauses_as_candidates(ctx: RunContext) -> None:
    provider = ClauseLibraryProvider()

    assert await provider.available(INTENT, ctx) is True
    contribution = await provider.contribute(INTENT, ctx)

    assert len(contribution.clause_candidates) == len(clauses_for("nda", "IN"))
    assert any(c.category == "confidentiality" for c in contribution.clause_candidates)


async def test_an_unknown_contract_type_is_unavailable(ctx: RunContext) -> None:
    exotic = IntentObject(contract_type="maritime_charter", confidence=0.9, jurisdiction="IN")
    assert await ClauseLibraryProvider().available(exotic, ctx) is False


def test_every_candidate_category_is_in_the_taxonomy() -> None:
    """A library clause must map to a real taxonomy category, never invent one — otherwise
    it becomes uncomparable with candidates from other sources."""
    for clause in clauses_for("nda", "IN") + clauses_for("service", "IN"):
        candidate = clause_to_candidate(clause)
        assert is_known(candidate.category), f"{clause.id} -> {candidate.category}"
        assert candidate.subcategory == clause.id.split(".", 1)[-1]


def test_required_clauses_are_marked_mandatory(ctx: RunContext) -> None:
    required = required_clause_ids("nda", "IN")
    candidates = [clause_to_candidate(c) for c in clauses_for("nda", "IN")]

    for candidate in candidates:
        clause_id = candidate.source_ref.clause_id
        assert candidate.mandatory == (clause_id in required)


# ---------------------------------------------------------------------- recommendation


async def test_recommendation_produces_and_persists_the_artifact(ctx: RunContext) -> None:
    contribution = await ClauseLibraryProvider().contribute(INTENT, ctx)
    cko = aggregate(
        (contribution,),
        INTENT,
        ResolutionPlan(providers=("clause_library", "llm")),
        contract_id=ctx.contract_id,
    )

    result = await recommend_clauses(cko, ctx)

    assert isinstance(result, ClauseRecommendationSet)
    assert result.recommendations, "every clause slot gets a recommendation"

    stored = await ArtifactStore(ctx.session_factory, ctx.contract_id).load(
        Artifact.CLAUSE_RECOMMENDATIONS
    )
    assert isinstance(stored, ClauseRecommendationSet)


async def test_a_slot_with_alternatives_records_the_runners_up(ctx: RunContext) -> None:
    """The audit trail: "why this clause?" is answered by the rejected alternatives.

    Two candidates for the same category — one a better jurisdiction fit — must produce a
    recommendation whose alternatives retain the loser and its score.
    """
    winner = ClauseCandidate(
        category="indemnity",
        applicability=("IN",),
        mandatory=True,
        source_ref=SourceRef(provider="clause_library", clause_id="lib.indemnity.in"),
    )
    loser = ClauseCandidate(
        category="indemnity",
        applicability=("US",),
        source_ref=SourceRef(provider="clause_library", clause_id="lib.indemnity.us"),
    )
    cko = aggregate(
        (_contribution_with(winner, loser),),
        INTENT,
        ResolutionPlan(providers=("clause_library", "llm")),
        contract_id=ctx.contract_id,
    )

    result = await recommend_clauses(cko, ctx)

    indemnity = next(r for r in result.recommendations if r.category == "indemnity")
    assert indemnity.chosen.source_ref.clause_id == "lib.indemnity.in"
    assert len(indemnity.alternatives) == 1
    assert indemnity.alternatives[0].candidate.source_ref.clause_id == "lib.indemnity.us"
    assert indemnity.alternatives[0].reasons or indemnity.alternatives[0].score == 0


async def test_a_single_candidate_slot_still_records_a_recommendation(ctx: RunContext) -> None:
    """One approved clause per slot is the common case. It is still recorded — as the only
    candidate, which is itself a fact worth keeping."""
    only = ClauseCandidate(
        category="governing_law",
        source_ref=SourceRef(provider="clause_library", clause_id="lib.gl"),
    )
    cko = aggregate(
        (_contribution_with(only),),
        INTENT,
        ResolutionPlan(providers=("clause_library", "llm")),
        contract_id=ctx.contract_id,
    )

    result = await recommend_clauses(cko, ctx)

    gl = next(r for r in result.recommendations if r.category == "governing_law")
    assert gl.chosen.source_ref.clause_id == "lib.gl"
    assert gl.alternatives == ()
    assert "only" in gl.rationale


# ----------------------------------------------------------------------------- helpers


def _contribution_with(*candidates: ClauseCandidate):  # type: ignore[no-untyped-def]
    from backend.schemas.cko import Provenance
    from backend.schemas.provider import KnowledgeContribution

    return KnowledgeContribution(
        provider="clause_library",
        provenance=Provenance(provider="clause_library"),
        clause_candidates=candidates,
    )
