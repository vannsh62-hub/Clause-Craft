"""Reference documents are analysed, never copied — proven end to end.

Spec 05 §7's test, and the milestone deliverable: a reference document carrying a
distinctive nonsense token, and that token must not appear in the generated output.

The guarantee is proven at two levels:

1. **Structural** — the reference *text* never enters Phase B. The drafting agent receives
   the CKO, whose `reference_knowledge` is `KnowledgeGraph` objects with no verbatim field,
   so the token is not in what the drafter is shown. This is the real guarantee: the drafter
   cannot copy what it never sees.
2. **The gate** — even if an analyzer misbehaved and copied a distinctive run into a graph
   field, `invariants/leakage.py` catches it at validation. Belt to the structural braces.

The token is chosen so that no clause library, prompt, or ordinary contract could contain
it by coincidence — a match is a copy, never a collision.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.core.config import settings
from backend.core.run_context import RunContext
from backend.invariants.leakage import find_leaks
from backend.knowledge.providers import reference as reference_mod
from backend.knowledge.providers.reference import ReferenceProvider
from backend.phase_a.aggregator import aggregate
from backend.phase_b import drafting as drafting_mod
from backend.phase_b.drafting import draft
from backend.runtime.adapters.openai_agents import OpenAIAgentsRuntime
from backend.schemas.intent import IntentObject, ResolutionPlan
from backend.schemas.plan import SectionDecision, TransformationPlan
from backend.storage import get_storage
from backend.workspace.models import Contract
from backend.workspace.store import REFERENCE_PREFIX, WorkspaceStore
from tests.fakes import FakeModel, Turn, text_message

#: Appears in no clause library, no prompt, no real contract. A match is a copy.
TOKEN = "ZORBLAX-QUUX-7719"

REFERENCE_TEXT = (
    "CONFIDENTIALITY AGREEMENT\n\n"
    f"The Receiving Party shall protect the {TOKEN} materials in strict confidence and "
    "shall not disclose them to any third party without prior written consent.\n\n"
    "This agreement is governed by the laws of India."
)

INTENT = IntentObject(contract_type="nda", confidence=0.9)


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


async def _add_reference(ctx: RunContext) -> None:
    async with ctx.session_factory() as s:
        await WorkspaceStore(s).write(
            ctx.contract_id, f"{REFERENCE_PREFIX}01-vendor-nda.txt", REFERENCE_TEXT
        )
        await s.commit()


# A well-behaved analyzer: it summarises, and its graph contains none of the source wording.
CLEAN_GRAPH = {
    "document": "01-vendor-nda.txt",
    "clause_categories": ["confidentiality", "governing_law"],
    "obligations": ["the receiving party must keep disclosed materials confidential"],
    "structure": ["confidentiality", "governing law"],
    "business_rules": [],
}


# ------------------------------------------------------------------- the analyzer's output


async def test_a_well_behaved_analyzer_produces_no_leak(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _add_reference(ctx)
    monkeypatch.setattr(
        reference_mod,
        "RUNTIME",
        OpenAIAgentsRuntime(FakeModel([Turn(output=[text_message(json.dumps(CLEAN_GRAPH))])])),
    )

    contribution = await ReferenceProvider().contribute(INTENT, ctx)

    graph_json = json.dumps([g.model_dump() for g in contribution.reference_knowledge])
    assert TOKEN not in graph_json, "the token must not survive analysis into the graph"


# ------------------------------------------------------- the structural guarantee, end to end


async def test_the_token_never_reaches_the_drafting_agent(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real guarantee: Phase B is never shown the reference text.

    The drafter receives the CKO. The reference text lives in the workspace and stays there;
    only the graph crosses the phase boundary. So the token cannot be in the drafter's
    input — it was never handed over.
    """
    await _add_reference(ctx)
    monkeypatch.setattr(
        reference_mod,
        "RUNTIME",
        OpenAIAgentsRuntime(FakeModel([Turn(output=[text_message(json.dumps(CLEAN_GRAPH))])])),
    )
    contribution = await ReferenceProvider().contribute(INTENT, ctx)

    cko = aggregate(
        (contribution,),
        INTENT,
        ResolutionPlan(providers=("reference", "llm")),
        contract_id=ctx.contract_id,
    )

    # Drive drafting with a spy model that records exactly what it is shown.
    drafter = FakeModel([Turn(output=[text_message('{"sections": [{"ref": "C", "text": "ok"}]}')])])
    monkeypatch.setattr(drafting_mod, "RUNTIME", OpenAIAgentsRuntime(drafter))

    plan = TransformationPlan(
        add=(SectionDecision(name="C", decision="add", reason="core NDA term"),)
    )
    from backend.artifacts import Artifact, ArtifactStore

    await ArtifactStore(ctx.session_factory, ctx.contract_id).save(
        Artifact.TRANSFORMATION_PLAN, plan
    )

    await draft(cko, ctx)

    shown = " ".join(
        c.input if isinstance(c.input, str) else json.dumps(c.input, default=str)
        for c in drafter.captures
    )
    assert TOKEN not in shown, "the reference token reached the drafting agent's context"


async def test_the_token_is_not_in_the_generated_document(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec §7's literal assertion: the token does not appear in the output."""
    await _add_reference(ctx)
    monkeypatch.setattr(
        reference_mod,
        "RUNTIME",
        OpenAIAgentsRuntime(FakeModel([Turn(output=[text_message(json.dumps(CLEAN_GRAPH))])])),
    )
    contribution = await ReferenceProvider().contribute(INTENT, ctx)
    cko = aggregate(
        (contribution,),
        INTENT,
        ResolutionPlan(providers=("reference", "llm")),
        contract_id=ctx.contract_id,
    )

    monkeypatch.setattr(
        drafting_mod,
        "RUNTIME",
        OpenAIAgentsRuntime(
            FakeModel(
                [Turn(output=[text_message('{"sections": [{"ref": "C", "text": "clean text"}]}')])]
            )
        ),
    )
    plan = TransformationPlan(add=(SectionDecision(name="C", decision="add", reason="term"),))
    from backend.artifacts import Artifact, ArtifactStore

    await ArtifactStore(ctx.session_factory, ctx.contract_id).save(
        Artifact.TRANSFORMATION_PLAN, plan
    )

    result = await draft(cko, ctx)

    from backend.invariants.docx_parse import block_texts

    body = " ".join(block_texts(get_storage().get(result.storage_key)).values())
    assert TOKEN not in body


# ---------------------------------------------------------------------------- the gate


def test_the_leakage_gate_catches_a_copy_that_slipped_through() -> None:
    """The second layer: if the structural guarantee were somehow bypassed, the gate fires.

    A draft that did contain a copied run is caught by `find_leaks` against the reference
    text — this is what runs at validation (M13) as the reference-leakage gate.
    """
    leaked_draft = (
        "1. Confidentiality\n\n"
        "The Receiving Party shall protect the ZORBLAX-QUUX-7719 materials in strict "
        "confidence and shall not disclose them to any third party without prior written "
        "consent.\n"
    )

    hits = find_leaks(leaked_draft, [("01-vendor-nda.txt", REFERENCE_TEXT)])

    assert hits, "a copied run must be caught"
    assert TOKEN.lower() in hits[0].passage


def test_the_gate_passes_a_clean_draft() -> None:
    """A draft that only shares ordinary phrasing is not a leak."""
    clean_draft = (
        "1. Confidentiality\n\n"
        "Each party will keep the other's information private and return it on termination.\n"
    )
    assert find_leaks(clean_draft, [("01-vendor-nda.txt", REFERENCE_TEXT)]) == ()


# ------------------------------------------------------------------ how a leak is reported


async def test_one_finding_per_document_however_many_passages_leaked(
    ctx: RunContext,
) -> None:
    """A copied clause trips the detector several times over.

    The user was shown the same sentence seven times — "a passage was copied verbatim from
    references/01-sla-agreement.txt" — which reports the same fact repeatedly and says
    nothing about how much was copied or what to do instead.
    """
    from backend.phase_b.validation_legal import _reference_leakage

    await _add_reference(ctx)
    leaked = "\n\n".join(REFERENCE_TEXT.split("\n\n")[:3])

    findings = await _reference_leakage(leaked, ctx)

    assert len(findings) == 1, "one finding names the source, however much was copied"
    finding = findings[0]
    assert "vendor-nda" in finding.message, "and it names the document readably"
    assert "for example" in finding.message, "with a sample the user can go and look at"
    assert "Use as a template" in (finding.fix_hint or ""), "and says what to do instead"


async def test_a_clean_draft_produces_no_finding(ctx: RunContext) -> None:
    from backend.phase_b.validation_legal import _reference_leakage

    await _add_reference(ctx)
    assert await _reference_leakage("A wholly original clause about nothing.", ctx) == []
