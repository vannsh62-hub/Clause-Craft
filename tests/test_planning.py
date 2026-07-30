"""Draft planning and transformation planning: the two Phase B agents that decide the shape
of a contract before any of it is written.

Transformation planning is the pivot — KEEP / MODIFY / REMOVE / ADD, decided from the CKO
alone. The starved context is deliberate and is tested: a planner shown the drafting agent's
reasoning would argue itself into agreement with it.
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
from backend.phase_b import planning as planning_mod
from backend.runtime.adapters.openai_agents import OpenAIAgentsRuntime
from backend.schemas.cko import ClauseCandidate, ContractKnowledgeObject, SemanticSection
from backend.schemas.intent import IntentObject, ResolutionPlan
from backend.schemas.plan import DraftPlan, TransformationPlan
from backend.schemas.playbook import PlaybookRequirement
from backend.workspace.models import Contract
from tests.fakes import FakeModel, Turn, text_message

DRAFT_PLAN = {
    "sections": [
        {
            "name": "Confidentiality",
            "order": 0,
            "rationale": "core NDA obligation",
            "source": "llm",
        },
        {"name": "Term", "order": 1, "rationale": "duration must be stated", "source": "llm"},
    ]
}
TRANSFORMATION = {
    "keep": [{"name": "Confidentiality", "decision": "keep", "reason": "applies to both"}],
    "modify": [{"name": "IP", "decision": "modify", "reason": "vendor licence"}],
    "remove": [{"name": "Working hours", "decision": "remove", "reason": "employment-specific"}],
    "add": [{"name": "Audit rights", "decision": "add", "reason": "playbook: vendor contracts"}],
}


def _cko(contract_id: uuid.UUID) -> ContractKnowledgeObject:
    """A template-mode CKO: it has a source document and sections read out of it.

    `source_storage_key` is what makes it template mode, and these tests need it: KEEP,
    MODIFY and REMOVE all name a block in a source document, so they are only meaningful
    when one exists. Generation mode derives its plan instead of asking a model.
    """
    return ContractKnowledgeObject(
        contract_id=contract_id,
        resolution=ResolutionPlan(providers=("playbook", "template", "llm")),
        intent=IntentObject(contract_type="nda", confidence=0.9),
        source_storage_key="fixture-source.docx",
        sections=(
            SemanticSection(block_id="b1", role="confidentiality", heading="Confidentiality"),
            SemanticSection(block_id="b2", role="working_hours", heading="Working Hours"),
        ),
        clause_candidates=(ClauseCandidate(category="confidentiality"),),
        playbook_rules=(
            PlaybookRequirement(rule_id="audit", kind="require_section", target="Audit rights"),
        ),
    )


@pytest_asyncio.fixture
async def ctx() -> AsyncIterator[RunContext]:
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    cid = uuid.uuid4()
    async with factory() as s:
        s.add(Contract(id=cid, contract_type="nda", request="Convert this NDA"))
        await s.commit()
    try:
        yield RunContext(contract_id=cid, session_factory=factory, contract_type="nda")
    finally:
        async with factory() as s:
            await s.execute(delete(Contract).where(Contract.id == cid))
            await s.commit()
        await engine.dispose()


def _use(monkeypatch: pytest.MonkeyPatch, payload: dict) -> FakeModel:
    fake = FakeModel([Turn(output=[text_message(json.dumps(payload))])])
    monkeypatch.setattr(planning_mod, "RUNTIME", OpenAIAgentsRuntime(fake))
    return fake


# ---------------------------------------------------------------------------- draft plan


async def test_draft_planning_produces_and_persists_the_plan(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, DRAFT_PLAN)

    plan = await planning_mod.plan_draft(_cko(ctx.contract_id), ctx)

    assert isinstance(plan, DraftPlan)
    assert [s.name for s in plan.sections] == ["Confidentiality", "Term"]

    stored = await ArtifactStore(ctx.session_factory, ctx.contract_id).load(Artifact.DRAFT_PLAN)
    assert isinstance(stored, DraftPlan)


# ------------------------------------------------------------------ transformation plan


async def test_generation_mode_carries_every_planned_section_into_the_transformation(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mode 1's contract is the draft plan; nothing may be lost between the two.

    The transformation planner sees the CKO alone, and in generation mode the CKO has no
    sections — so a model asked to classify them returned only what it could infer from
    playbook requirements, and the run finalized a contract consisting of one clause. Here
    the plan is derived, so every planned section survives as an ADD.
    """
    _use(monkeypatch, DRAFT_PLAN)
    generation = _cko(ctx.contract_id).model_copy(update={"source_storage_key": None})
    draft_plan = await planning_mod.plan_draft(generation, ctx)

    plan = await planning_mod.plan_transformation(generation, ctx)

    assert [d.name for d in plan.add] == [s.name for s in draft_plan.sections]
    assert not plan.keep and not plan.modify and not plan.remove, "nothing to keep without a source"


async def test_transformation_planning_produces_the_four_decision_kinds(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, TRANSFORMATION)

    plan = await planning_mod.plan_transformation(_cko(ctx.contract_id), ctx)

    assert isinstance(plan, TransformationPlan)
    assert [d.name for d in plan.keep] == ["Confidentiality"]
    assert [d.name for d in plan.modify] == ["IP"]
    assert [d.name for d in plan.remove] == ["Working hours"]
    assert [d.name for d in plan.add] == ["Audit rights"]


async def test_the_transformation_artifact_is_the_one_drafting_requires(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It must land at exactly the path the drafting precondition reads."""
    _use(monkeypatch, TRANSFORMATION)

    await planning_mod.plan_transformation(_cko(ctx.contract_id), ctx)

    stored = await ArtifactStore(ctx.session_factory, ctx.contract_id).load(
        Artifact.TRANSFORMATION_PLAN
    )
    assert isinstance(stored, TransformationPlan)
    assert stored.all_decisions, "the plan the drafter reads carries real decisions"


async def test_the_transformation_planner_is_not_shown_a_draft_or_a_rationale(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Starved context, asserted on what the model was actually shown.

    The planner receives the CKO. It must not receive a draft, a drafting rationale, or the
    draft plan's prose — a planner that sees the drafting agent's reasoning argues itself
    into agreement, and the point of classifying first is to decide independently.
    """
    fake = _use(monkeypatch, TRANSFORMATION)

    await planning_mod.plan_transformation(_cko(ctx.contract_id), ctx)

    # The *input* only — not `as_text()`, which folds in the system prompt. The prompt
    # legitimately mentions "the drafting agent's job"; what must not appear is a draft or a
    # rationale in the data the planner is handed.
    shown = " ".join(
        c.input if isinstance(c.input, str) else json.dumps(c.input, default=str)
        for c in fake.captures
    ).lower()
    assert "confidentiality" in shown, "it did see the CKO's sections"
    for leak in ("draft_v", "please award", "rationale for the draft", "the draft says"):
        assert leak not in shown


# ------------------------------------------------------------------------- agent specs


def test_the_pivot_gets_the_strongest_model() -> None:
    """Transformation planning is the pivot and is configured as the priority.

    Not a style point: a weaker model here produces a plan that keeps too little, and a plan
    that keeps too little regenerates the document instead of converting it.
    """
    assert (
        planning_mod.build_transformation_spec().model == settings.transformation_model == "gpt-4.1"
    )
    assert planning_mod.build_transformation_spec().temperature == 0.0


def test_both_planners_are_configured_for_their_output() -> None:
    assert planning_mod.build_draft_plan_spec().output_model is DraftPlan
    assert planning_mod.build_transformation_spec().output_model is TransformationPlan
    for spec in (planning_mod.build_draft_plan_spec(), planning_mod.build_transformation_spec()):
        assert spec.tools == ()
