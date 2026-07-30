"""The whole system end to end: a request goes in, a contract comes out.

Every stage has been tested in isolation. This drives them together — intent, resolution,
gather, understanding, aggregation, planning, transformation, recommendation, drafting,
validation, finalization — with fake models at each agent, and asserts the pipeline reaches
a coherent outcome and leaves the full artifact trail behind.

It is the proof that the parts compose, which no single-stage test can give.
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
from backend.phase_a import intent as intent_mod
from backend.phase_b import drafting as drafting_mod
from backend.phase_b import planning as planning_mod
from backend.pipeline import run_pipeline
from backend.runtime.adapters.openai_agents import OpenAIAgentsRuntime
from backend.schemas.cko import ContractKnowledgeObject
from backend.workspace.models import Contract, ContractVersion
from tests.fakes import FakeModel, Turn, text_message


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


def _one(payload: dict) -> OpenAIAgentsRuntime:
    return OpenAIAgentsRuntime(FakeModel([Turn(output=[text_message(json.dumps(payload))])]))


def _wire(monkeypatch: pytest.MonkeyPatch, *, confidence: float = 0.95) -> None:
    """Fake models for every agent the pipeline calls, in Mode 1 (no template)."""
    intent = {
        "contract_type": "nda",
        "parties": [{"name": "ProcBay", "role": "Disclosing Party"}],
        "jurisdiction": "IN",
        "purpose": "protect disclosures",
        "mode": "ai_drafting",
        "confidence": confidence,
        "needs_clarification": [],
    }
    monkeypatch.setattr(intent_mod, "RUNTIME", _one(intent))

    draft_plan = {
        "sections": [
            {"name": "Confidentiality", "order": 0, "rationale": "core", "source": "llm"},
            {"name": "Data Protection", "order": 1, "rationale": "DPDP", "source": "playbook"},
            {"name": "Governing Law", "order": 2, "rationale": "required", "source": "playbook"},
        ]
    }
    transformation = {
        "add": [
            {"name": "Confidentiality", "decision": "add", "reason": "core NDA clause"},
            {"name": "Data Protection", "decision": "add", "reason": "playbook: DPDP"},
            {"name": "Governing Law", "decision": "add", "reason": "playbook: governing law"},
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

    drafted = {
        "sections": [
            {"ref": "Confidentiality", "text": "The receiving party shall keep it secret."},
            {"ref": "Data Protection", "text": "Personal data is handled under the DPDP Act."},
            {"ref": "Governing Law", "text": "Governed by the laws of India."},
        ]
    }
    monkeypatch.setattr(drafting_mod, "RUNTIME", _one(drafted))


# --------------------------------------------------------------------------- the run


async def test_a_request_becomes_a_finalized_contract(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch)

    outcome = await run_pipeline(
        "Draft an NDA between ProcBay and Acme, governed by Indian law.", ctx
    )

    assert outcome.status == "complete", outcome.message


async def test_the_full_artifact_trail_is_left_behind(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase A's understanding and Phase B's drafting, both on disk — the explainability
    the whole architecture exists for."""
    _wire(monkeypatch)

    await run_pipeline("Draft an NDA.", ctx)

    artifacts = ArtifactStore(ctx.session_factory, ctx.contract_id)
    for artifact in (
        Artifact.INTENT,
        Artifact.RESOLUTION,
        Artifact.CKO,
        Artifact.DRAFT_PLAN,
        Artifact.TRANSFORMATION_PLAN,
        Artifact.VALIDATION_LEGAL,
        Artifact.VALIDATION_DOCUMENT,
    ):
        assert await artifacts.exists(artifact), f"{artifact.name} was not produced"


async def test_the_cko_is_the_phase_boundary_and_is_persisted(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch)
    await run_pipeline("Draft an NDA governed by Indian law.", ctx)

    cko = await ArtifactStore(ctx.session_factory, ctx.contract_id).load(Artifact.CKO)
    assert isinstance(cko, ContractKnowledgeObject)
    # The playbook fired (jurisdiction IN → DPDP), and its requirement is in the CKO.
    assert any(r.rule_id == "dpdp-in" for r in cko.playbook_rules)


async def test_a_draft_is_recorded(ctx: RunContext, monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy import select

    _wire(monkeypatch)
    await run_pipeline("Draft an NDA.", ctx)

    async with ctx.session_factory() as s:
        version = (
            await s.execute(
                select(ContractVersion).where(ContractVersion.contract_id == ctx.contract_id)
            )
        ).scalar_one()
    assert version.attempt == 1
    assert "India" in version.markdown


# ------------------------------------------------------------------------- suspension


async def test_a_low_confidence_request_asks_instead_of_drafting(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase A's refusal reaches the pipeline outcome as `awaiting_input` — no draft is
    produced when the request is not safe to act on."""
    _wire(monkeypatch, confidence=0.3)

    outcome = await run_pipeline("Draft me something for the Acme deal.", ctx)

    assert outcome.status == "awaiting_input"
    assert outcome.questions

    from sqlalchemy import func, select

    async with ctx.session_factory() as s:
        drafts = (
            await s.execute(
                select(func.count())
                .select_from(ContractVersion)
                .where(ContractVersion.contract_id == ctx.contract_id)
            )
        ).scalar_one()
    assert drafts == 0, "nothing was drafted before the question was answered"


# ------------------------------------------------------------------------- events


async def test_stage_events_are_emitted(ctx: RunContext, monkeypatch: pytest.MonkeyPatch) -> None:
    """The SSE surface has something to stream. A recording publisher captures the stages."""
    _wire(monkeypatch)

    class RecordingPublisher:
        def __init__(self) -> None:
            self.stages: list[tuple[str, str]] = []

        async def stage(self, name: str, status: str, **detail: object) -> int:
            self.stages.append((name, status))
            return len(self.stages)

        async def emit(self, event_type: str, **payload: object) -> int:  # pragma: no cover
            return 0

    publisher = RecordingPublisher()
    await run_pipeline("Draft an NDA.", ctx, publisher)  # type: ignore[arg-type]

    names = [name for name, _ in publisher.stages]
    assert "phase_a" in names
    assert "phase_b" in names
    assert ("phase_b", "complete") in publisher.stages
