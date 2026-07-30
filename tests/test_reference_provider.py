"""The reference provider: analyse uploaded documents into knowledge, in parallel.

Behaviour of the provider itself. The leakage guarantee — that reference *text* never
reaches the output — is `test_reference_leakage.py`; this file covers availability,
parallelism, and the shape of what is contributed.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.core.config import settings
from backend.core.run_context import RunContext
from backend.knowledge.providers import reference as reference_mod
from backend.knowledge.providers.reference import ReferenceProvider
from backend.runtime.adapters.openai_agents import OpenAIAgentsRuntime
from backend.runtime.spec import AgentResult
from backend.schemas.cko import KnowledgeGraph
from backend.schemas.intent import IntentObject
from backend.workspace.models import Contract
from backend.workspace.store import REFERENCE_PREFIX, WorkspaceStore
from tests.fakes import FakeModel, Turn, text_message

INTENT = IntentObject(contract_type="nda", confidence=0.9)

GRAPH = {
    "document": "vendor-nda.txt",
    "clause_categories": ["confidentiality", "term"],
    "obligations": ["the receiving party must return materials on termination"],
    "structure": ["recitals", "confidentiality", "term", "signatures"],
    "business_rules": [],
}


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


async def _add_reference(ctx: RunContext, name: str, text: str) -> None:
    async with ctx.session_factory() as s:
        await WorkspaceStore(s).write(ctx.contract_id, f"{REFERENCE_PREFIX}{name}", text)
        await s.commit()


def _use(monkeypatch: pytest.MonkeyPatch, *graphs: dict) -> None:
    turns = [Turn(output=[text_message(json.dumps(g))]) for g in graphs]
    monkeypatch.setattr(reference_mod, "RUNTIME", OpenAIAgentsRuntime(FakeModel(turns)))


# ------------------------------------------------------------------------- availability


async def test_no_references_means_the_provider_is_unavailable(ctx: RunContext) -> None:
    assert await ReferenceProvider().available(INTENT, ctx) is False


async def test_an_uploaded_reference_makes_it_available(ctx: RunContext) -> None:
    await _add_reference(ctx, "01-vendor-nda.txt", "Some reference text.")
    assert await ReferenceProvider().available(INTENT, ctx) is True


# ------------------------------------------------------------------------- contributing


async def test_it_contributes_a_knowledge_graph(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _add_reference(ctx, "01-vendor-nda.txt", "Confidential information ...")
    _use(monkeypatch, GRAPH)

    contribution = await ReferenceProvider().contribute(INTENT, ctx)

    assert contribution.provider == "reference"
    assert len(contribution.reference_knowledge) == 1
    graph = contribution.reference_knowledge[0]
    assert isinstance(graph, KnowledgeGraph)
    assert "confidentiality" in graph.clause_categories


async def test_it_contributes_no_verbatim_text_fields(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The structural guarantee, at the type level.

    `KnowledgeGraph` has no field that can hold a clause. This test documents that the
    contribution carries only the graph — not the reference text, not a section field that
    could smuggle it.
    """
    await _add_reference(ctx, "01-vendor-nda.txt", "text")
    _use(monkeypatch, GRAPH)

    contribution = await ReferenceProvider().contribute(INTENT, ctx)

    assert contribution.sections == ()
    assert contribution.formatting is None
    assert contribution.clause_candidates == ()
    # Every field of the graph is a summary type; none is raw document text.
    assert set(KnowledgeGraph.model_fields) == {
        "document",
        "clause_categories",
        "obligations",
        "terminology",
        "structure",
        "negotiation_patterns",
        "business_rules",
    }


async def test_several_documents_each_get_a_graph(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _add_reference(ctx, "01-a.txt", "First reference.")
    await _add_reference(ctx, "02-b.txt", "Second reference.")
    _use(monkeypatch, {**GRAPH, "document": "a"}, {**GRAPH, "document": "b"})

    contribution = await ReferenceProvider().contribute(INTENT, ctx)

    assert len(contribution.reference_knowledge) == 2


async def test_each_analyzer_sees_only_its_own_document(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One analyzer per document — not one analyzer shown all of them.

    Cross-contaminating documents would let one reference's terms bleed into another's
    graph, and would make the parallel fan-out pointless.
    """
    await _add_reference(ctx, "01-alpha.txt", "ALPHA-ONLY-TOKEN appears here.")
    await _add_reference(ctx, "02-beta.txt", "BETA-ONLY-TOKEN appears here.")
    fake = FakeModel([Turn(output=[text_message(json.dumps(GRAPH))]) for _ in range(2)])
    monkeypatch.setattr(reference_mod, "RUNTIME", OpenAIAgentsRuntime(fake))

    await ReferenceProvider().contribute(INTENT, ctx)

    inputs = [
        c.input if isinstance(c.input, str) else json.dumps(c.input, default=str)
        for c in fake.captures
    ]
    alpha = next(i for i in inputs if "ALPHA-ONLY-TOKEN" in i)
    beta = next(i for i in inputs if "BETA-ONLY-TOKEN" in i)
    assert "BETA-ONLY-TOKEN" not in alpha
    assert "ALPHA-ONLY-TOKEN" not in beta


# --------------------------------------------------------------------------- parallel


async def test_documents_are_analysed_concurrently(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Timing evidence. Three documents that each take a fixed delay finish in roughly one
    delay, not three — reference analysis is slow and the documents are independent."""
    for n in range(3):
        await _add_reference(ctx, f"0{n}-ref.txt", f"Reference {n}.")

    class SlowRuntime:
        name = "slow"

        async def run(self, spec: Any, c: Any, instruction: str, *, history: Any = None) -> Any:
            await asyncio.sleep(0.2)
            return AgentResult(text="{}", output=KnowledgeGraph(document="x"))

        async def run_many(self, jobs: Any, c: Any) -> Any:
            return list(await asyncio.gather(*(self.run(s, c, t) for s, t in jobs)))

    monkeypatch.setattr(reference_mod, "RUNTIME", SlowRuntime())

    start = time.monotonic()
    await ReferenceProvider().contribute(INTENT, ctx)
    elapsed = time.monotonic() - start

    assert elapsed < 0.5, f"three 0.2s analyses took {elapsed:.2f}s — they serialised"


# ------------------------------------------------------------------------- the spec


def test_the_analyzer_output_type_has_no_verbatim_slot() -> None:
    """The guarantee is in the schema. If a future edit adds a `text` field to
    KnowledgeGraph, the drafter could be handed reference wording, and this fails."""
    spec = reference_mod.build_reference_spec()
    assert spec.output_model is KnowledgeGraph
    assert "text" not in KnowledgeGraph.model_fields
    assert "raw" not in KnowledgeGraph.model_fields
    assert "verbatim" not in KnowledgeGraph.model_fields
