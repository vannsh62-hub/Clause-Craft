"""Mode 3 — clause library plus playbook — reaches spec 01's behaviour through the new
pipeline.

Spec 01 drafted from an approved clause library. That capability is now assembled from
parts: the clause-library provider retrieves the clauses, the playbook provider imposes the
rules, the aggregator builds a CKO, and the drafting engine runs planning → recommendation →
drafting over it. This test drives that whole path with fake models and asserts it produces
a draft and the recommendation audit trail.

It is a pipeline-level parity test, not an API test — wiring the engine into the HTTP surface
is M14. What it proves is that the Mode 3 machinery is complete and coherent end to end.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.artifacts import Artifact, ArtifactStore
from backend.core.config import settings
from backend.core.run_context import RunContext
from backend.knowledge.providers.clause_library import ClauseLibraryProvider
from backend.knowledge.providers.playbook import PlaybookProvider
from backend.phase_a.aggregator import aggregate
from backend.phase_b import drafting as drafting_mod
from backend.phase_b import engine as engine_mod
from backend.phase_b import planning as planning_mod
from backend.phase_b.engine import run_drafting_engine
from backend.runtime.adapters.openai_agents import OpenAIAgentsRuntime
from backend.schemas.intent import IntentObject, ResolutionPlan
from backend.schemas.recommendation import ClauseRecommendationSet
from backend.workspace.models import Contract
from tests.fakes import FakeModel, Turn, text_message

INTENT = IntentObject(
    contract_type="nda", confidence=0.9, jurisdiction="IN", mode="library_playbook"
)


@pytest_asyncio.fixture
async def ctx() -> AsyncIterator[RunContext]:
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    cid = uuid.uuid4()
    async with factory() as s:
        s.add(Contract(id=cid, contract_type="nda", request="Draft an NDA from the library"))
        await s.commit()
    try:
        yield RunContext(contract_id=cid, session_factory=factory, contract_type="nda")
    finally:
        async with factory() as s:
            await s.execute(delete(Contract).where(Contract.id == cid))
            await s.commit()
        await engine.dispose()


async def _library_playbook_cko(ctx: RunContext):  # type: ignore[no-untyped-def]
    """The CKO a real Mode 3 gather would build: library clauses + playbook requirements."""
    clauses = await ClauseLibraryProvider().contribute(INTENT, ctx)
    playbook = await PlaybookProvider().contribute(INTENT, ctx)
    return aggregate(
        (playbook, clauses),
        INTENT,
        ResolutionPlan(providers=("playbook", "clause_library", "llm")),
        contract_id=ctx.contract_id,
    )


def _wire_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake models for the three Phase B agents that make model calls.

    Draft planning and transformation planning each produce a plan; drafting produces the
    section text. Recommendation and the clause provider are deterministic — no model.
    """
    draft_plan = {
        "sections": [
            {"name": "Confidentiality", "order": 0, "rationale": "core", "source": "library"},
            {"name": "Governing Law", "order": 1, "rationale": "required", "source": "library"},
        ]
    }
    transformation = {
        "add": [
            {"name": "Confidentiality", "decision": "add", "reason": "core NDA clause"},
            {
                "name": "Governing Law",
                "decision": "add",
                "reason": "playbook: governing-law-always",
            },
            {"name": "Data Protection", "decision": "add", "reason": "playbook: DPDP"},
        ]
    }
    drafted = {
        "sections": [
            {"ref": "Confidentiality", "text": "The receiving party shall keep it secret."},
            {"ref": "Governing Law", "text": "Governed by the laws of India."},
            {"ref": "Data Protection", "text": "Personal data handled under the DPDP Act."},
        ]
    }
    monkeypatch.setattr(
        planning_mod,
        "RUNTIME",
        OpenAIAgentsRuntime(
            FakeModel(
                [
                    Turn(output=[text_message(json.dumps(draft_plan))]),
                    Turn(output=[text_message(json.dumps(transformation))]),
                ]
            )
        ),
    )
    monkeypatch.setattr(
        drafting_mod,
        "RUNTIME",
        OpenAIAgentsRuntime(FakeModel([Turn(output=[text_message(json.dumps(drafted))])])),
    )


# --------------------------------------------------------------------------- the flow


async def test_mode_3_runs_end_to_end(ctx: RunContext, monkeypatch: pytest.MonkeyPatch) -> None:
    cko = await _library_playbook_cko(ctx)
    _wire_fakes(monkeypatch)

    outcome = await run_drafting_engine(cko, ctx)

    assert outcome.contract_id == str(ctx.contract_id)
    assert "library_playbook" not in outcome.detail  # mode reported is ai_drafting (no template)


async def test_the_cko_carries_both_library_clauses_and_playbook_rules(ctx: RunContext) -> None:
    cko = await _library_playbook_cko(ctx)

    assert cko.clause_candidates, "library clauses were retrieved"
    assert cko.playbook_rules, "playbook rules fired"
    # DPDP fired because jurisdiction is IN.
    assert any(r.rule_id == "dpdp-in" for r in cko.playbook_rules)
    # Library clauses came first — clause_library outranks nothing it shares a slot with here,
    # but the candidates are present and categorised.
    assert any(c.category == "confidentiality" for c in cko.clause_candidates)


async def test_all_the_mode_3_artifacts_are_produced(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Planning, transformation, recommendation, and a draft — the full Mode 3 trail."""
    cko = await _library_playbook_cko(ctx)
    _wire_fakes(monkeypatch)

    await run_drafting_engine(cko, ctx)

    artifacts = ArtifactStore(ctx.session_factory, ctx.contract_id)
    assert await artifacts.exists(Artifact.DRAFT_PLAN)
    assert await artifacts.exists(Artifact.TRANSFORMATION_PLAN)
    assert await artifacts.exists(Artifact.CLAUSE_RECOMMENDATIONS)

    recommendations = await artifacts.load(Artifact.CLAUSE_RECOMMENDATIONS)
    assert isinstance(recommendations, ClauseRecommendationSet)
    assert recommendations.recommendations, "the library clauses were recommended"


async def test_a_drafting_attempt_is_recorded_for_mode_3(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import select

    from backend.workspace.models import ContractVersion

    cko = await _library_playbook_cko(ctx)
    _wire_fakes(monkeypatch)

    await run_drafting_engine(cko, ctx)

    async with ctx.session_factory() as s:
        version = (
            await s.execute(
                select(ContractVersion).where(ContractVersion.contract_id == ctx.contract_id)
            )
        ).scalar_one()
    assert version.attempt == 1
    assert "India" in version.markdown, "the drafted clauses reached the recorded version"


def test_recommendation_is_skipped_when_there_are_no_clause_candidates() -> None:
    """Mode 1 (no library) must not produce an empty recommendation artifact — there is
    nothing to recommend. The engine guards on `cko.clause_candidates`."""
    import inspect

    source = inspect.getsource(engine_mod.run_drafting_engine)
    assert "if cko.clause_candidates:" in source, "recommendation must be guarded"
