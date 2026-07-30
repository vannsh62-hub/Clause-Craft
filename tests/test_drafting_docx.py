"""Drafting produces a real document, and Mode 2 preserves the uploaded formatting.

This is where the transformation plan becomes a DOCX. The M2 fidelity machinery
(`apply_transformation`, `compare`) finally has a caller, and the milestone's promise —
upload an SLA, get back a converted SLA with its formatting intact — is exercised end to
end with a fake model.

Mode 2 (a template was uploaded) edits the document in place: KEEP sections are never
touched, so their formatting survives by construction. Mode 1 (no template) generates a
document from scratch. Both are tested; the fidelity assertion is Mode 2, because Mode 1 has
no prior formatting to preserve.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.artifacts import Artifact, ArtifactStore
from backend.core.config import settings
from backend.core.run_context import RunContext
from backend.invariants.docx_fidelity import compare
from backend.invariants.docx_parse import block_texts, parse_docx
from backend.phase_b import drafting as drafting_mod
from backend.phase_b.drafting import draft
from backend.runtime.adapters.openai_agents import OpenAIAgentsRuntime
from backend.schemas.cko import ContractKnowledgeObject, SourceRef
from backend.schemas.intent import IntentObject, ResolutionPlan
from backend.schemas.plan import SectionDecision, TransformationPlan
from backend.storage import get_storage
from backend.workspace.models import Contract, ContractVersion
from tests.fakes import FakeModel, Turn, text_message

FIXTURE = Path(__file__).parent / "data" / "sla-sample.docx"


@pytest.fixture(scope="module")
def docx() -> bytes:
    return FIXTURE.read_bytes()


@pytest_asyncio.fixture
async def ctx() -> AsyncIterator[RunContext]:
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    cid = uuid.uuid4()
    async with factory() as s:
        s.add(Contract(id=cid, contract_type="sla", request="Convert this SLA"))
        await s.commit()
    try:
        yield RunContext(contract_id=cid, session_factory=factory, contract_type="sla")
    finally:
        async with factory() as s:
            await s.execute(delete(Contract).where(Contract.id == cid))
            await s.commit()
        await engine.dispose()


def _ref(block_id: str) -> SourceRef:
    return SourceRef(provider="template", block_id=block_id)


def _use(monkeypatch: pytest.MonkeyPatch, drafted: dict) -> None:
    fake = FakeModel([Turn(output=[text_message(json.dumps(drafted))])])
    monkeypatch.setattr(drafting_mod, "RUNTIME", OpenAIAgentsRuntime(fake))


async def _store_source(docx: bytes) -> str:
    key = f"{uuid.uuid4()}.docx"
    get_storage().put(key, docx)
    return key


# --------------------------------------------------------------- Mode 2, end to end


async def test_mode_2_converts_the_upload_and_preserves_formatting(
    ctx: RunContext, docx: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The milestone. Upload an SLA, plan a conversion, get back a document whose KEEP
    sections are byte-for-byte unchanged at the paragraph-style level."""
    texts = block_texts(docx)
    termination = next(k for k, v in texts.items() if "terminate on 30 days" in v)
    sub_item = next(k for k, v in texts.items() if "Excluding maintenance" in v)
    kept = [k for k, v in texts.items() if "Uptime shall be" in v or "Fee Schedule" in v]

    key = await _store_source(docx)
    plan = TransformationPlan(
        keep=tuple(
            SectionDecision(name=f"keep-{i}", decision="keep", reason="applies", source_ref=_ref(k))
            for i, k in enumerate(kept)
        ),
        modify=(
            SectionDecision(
                name="Termination",
                decision="modify",
                reason="90 days",
                source_ref=_ref(termination),
            ),
        ),
        remove=(
            SectionDecision(
                name="Maintenance", decision="remove", reason="n/a", source_ref=_ref(sub_item)
            ),
        ),
    )
    await ArtifactStore(ctx.session_factory, ctx.contract_id).save(
        Artifact.TRANSFORMATION_PLAN, plan
    )

    # The drafting agent supplies text for the one MODIFY, keyed by its block id.
    _use(
        monkeypatch,
        {
            "sections": [
                {"ref": termination, "text": "Either party may terminate on 90 days notice."}
            ]
        },
    )

    cko = _cko_with_source(ctx.contract_id, key)
    result = await draft(cko, ctx)

    assert result.mode == "template"
    edited = get_storage().get(result.storage_key)

    # The conversion happened.
    new_texts = " ".join(block_texts(edited).values())
    assert "90 days" in new_texts
    assert "Excluding maintenance" not in new_texts

    # And the KEEP sections are untouched — the whole promise of Mode 2.
    fidelity = compare(docx, edited, expect_unchanged=tuple(kept))
    assert fidelity.unchanged, f"KEEP sections were disturbed: {fidelity}"


async def test_the_result_is_a_parseable_docx(
    ctx: RunContext, docx: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = await _store_source(docx)
    plan = TransformationPlan(
        keep=(
            SectionDecision(
                name="k",
                decision="keep",
                reason="r",
                source_ref=_ref(next(iter(block_texts(docx)))),
            ),
        )
    )
    await ArtifactStore(ctx.session_factory, ctx.contract_id).save(
        Artifact.TRANSFORMATION_PLAN, plan
    )
    _use(monkeypatch, {"sections": []})

    result = await draft(_cko_with_source(ctx.contract_id, key), ctx)

    # It re-parses — a mangled package would raise here.
    reparsed = parse_docx(
        get_storage().get(result.storage_key), filename="out.docx", storage_key="k"
    )
    assert reparsed.formatting.blocks


async def test_a_drafting_attempt_is_recorded(
    ctx: RunContext, docx: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = await _store_source(docx)
    plan = TransformationPlan(
        modify=(
            SectionDecision(
                name="T",
                decision="modify",
                reason="r",
                source_ref=_ref(next(k for k, v in block_texts(docx).items() if "terminate" in v)),
            ),
        )
    )
    await ArtifactStore(ctx.session_factory, ctx.contract_id).save(
        Artifact.TRANSFORMATION_PLAN, plan
    )
    ref = next(k for k, v in block_texts(docx).items() if "terminate" in v)
    _use(monkeypatch, {"sections": [{"ref": ref, "text": "Terminate on 90 days notice."}]})

    result = await draft(_cko_with_source(ctx.contract_id, key), ctx)

    async with ctx.session_factory() as s:
        version = (
            await s.execute(
                select(ContractVersion).where(ContractVersion.contract_id == ctx.contract_id)
            )
        ).scalar_one()
    assert version.attempt == result.attempt == 1
    assert version.path == "draft_v1.docx"


# ------------------------------------------------------------------------- Mode 1


async def test_mode_1_generates_a_document_with_no_template(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No uploaded document, so nothing to preserve — the plan is all ADD and the document
    is generated."""
    plan = TransformationPlan(
        add=(
            SectionDecision(name="Confidentiality", decision="add", reason="core NDA term"),
            SectionDecision(name="Term", decision="add", reason="duration required"),
        )
    )
    await ArtifactStore(ctx.session_factory, ctx.contract_id).save(
        Artifact.TRANSFORMATION_PLAN, plan
    )
    _use(
        monkeypatch,
        {
            "sections": [
                {"ref": "Confidentiality", "text": "The parties shall keep information secret."},
                {"ref": "Term", "text": "This agreement runs for two years."},
            ]
        },
    )

    cko = _cko_no_source(ctx.contract_id)
    result = await draft(cko, ctx)

    assert result.mode == "ai_drafting"
    generated = get_storage().get(result.storage_key)
    reparsed = parse_docx(generated, filename="out.docx", storage_key="k")
    assert reparsed.formatting.blocks
    assert "keep information secret" in " ".join(block_texts(generated).values())


# --------------------------------------------------------------------------- refusals


async def test_a_modify_the_agent_did_not_fill_is_refused(
    ctx: RunContext, docx: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`apply_transformation` refuses a MODIFY with no supplied text — an empty clause in an
    executed contract is worse than a failed run."""
    from backend.invariants.docx_parse import TemplateError

    key = await _store_source(docx)
    ref = next(k for k, v in block_texts(docx).items() if "terminate" in v)
    plan = TransformationPlan(
        modify=(SectionDecision(name="T", decision="modify", reason="r", source_ref=_ref(ref)),)
    )
    await ArtifactStore(ctx.session_factory, ctx.contract_id).save(
        Artifact.TRANSFORMATION_PLAN, plan
    )
    _use(monkeypatch, {"sections": []})  # the agent supplied nothing

    with pytest.raises(TemplateError, match="No replacement text"):
        await draft(_cko_with_source(ctx.contract_id, key), ctx)


# ----------------------------------------------------------------------------- helpers


def _cko_with_source(contract_id: uuid.UUID, storage_key: str) -> ContractKnowledgeObject:
    from backend.schemas.template import BlockFingerprint, FormattingManifest

    return ContractKnowledgeObject(
        contract_id=contract_id,
        resolution=ResolutionPlan(providers=("template", "llm")),
        intent=IntentObject(contract_type="sla", confidence=0.9, mode="template"),
        formatting=FormattingManifest(
            blocks=(BlockFingerprint(index=0, kind="paragraph", text_sha="a"),)
        ),
        source_storage_key=storage_key,
    )


def _cko_no_source(contract_id: uuid.UUID) -> ContractKnowledgeObject:
    return ContractKnowledgeObject(
        contract_id=contract_id,
        resolution=ResolutionPlan(providers=("llm",)),
        intent=IntentObject(contract_type="nda", confidence=0.9, mode="ai_drafting"),
    )
