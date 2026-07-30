"""Contract Intelligence Engine: deterministic splitting/analytics/health-score, the
content-hash cache, and `POST /contracts/{id}/intelligence`.

Splitting and analytics are pure functions — covered directly, no fake model needed
(same convention as `/clauses/analyse`). The endpoint goes through the one-call
analysis agent, so those tests wire a `FakeModel` the same way
`tests/test_clause_fill_details_api.py` drives `fill_agent`.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from backend.api.deps import get_session_factory
from backend.api.main import app
from backend.phase_b import intelligence as engine
from backend.runtime.adapters.openai_agents import OpenAIAgentsRuntime
from backend.subagents.contract_intelligence import intelligence_agent
from backend.workspace.models import Contract
from tests.fakes import FakeModel, Turn, text_message

DOC = """# NDA

## 1. Payment
Pay {{ amount }} within 30 days. This clause has more than one sentence in it. Here is another.

## 2. Termination
Either party may terminate this agreement on notice.
"""


# ------------------------------------------------------------------------ split_clauses


def test_split_clauses_matches_frontend_heading_rule() -> None:
    sections = engine.split_clauses(DOC)
    assert [s.title for s in sections] == ["Payment", "Termination"]
    assert sections[0].markdown.startswith("## 1. Payment")


def test_split_clauses_on_empty_document() -> None:
    assert engine.split_clauses("") == []


# ------------------------------------------------------------------------ analytics


def test_clause_analytics_counts_words_and_variables() -> None:
    section = engine.split_clauses(DOC)[0]
    analytics = engine._clause_analytics(section, cross_ref_count=2, suggestion_count=1)
    assert analytics.variables == 1
    assert analytics.words > 0
    assert analytics.cross_references == 2
    assert analytics.ai_suggestions == 1


def test_readability_heuristic_short_sentences_is_good() -> None:
    assert engine._readability(words=20, sentence_count=4) == "Good"
    assert engine._readability(words=0, sentence_count=0) == "Good"


# ------------------------------------------------------------------------ cache


def test_cache_key_is_stable_for_same_document_and_perspective() -> None:
    assert engine._cache_key(DOC, "neutral") == engine._cache_key(DOC, "neutral")
    assert engine._cache_key(DOC, "vendor") != engine._cache_key(DOC, "neutral")


def test_invalidate_cache_clears_everything_with_no_args() -> None:
    key = engine._cache_key(DOC, "neutral")
    engine._cache[key] = (0.0, None)  # type: ignore[assignment]
    engine.invalidate_cache()
    assert engine._cache_get(key) is None


def test_invalidate_cache_drops_one_entry() -> None:
    key_a = engine._cache_key(DOC, "neutral")
    key_b = engine._cache_key(DOC, "vendor")
    engine._cache[key_a] = (0.0, None)  # type: ignore[assignment]
    engine._cache[key_b] = (0.0, None)  # type: ignore[assignment]
    engine.invalidate_cache(DOC, "neutral")
    assert engine._cache_get(key_a) is None
    assert key_b in engine._cache
    engine._cache.pop(key_b, None)


# ------------------------------------------------------------------------ endpoint


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def contract() -> AsyncIterator[Contract]:
    factory = get_session_factory()
    row = Contract(id=uuid.uuid4(), contract_type="nda", request="an NDA", variables={})
    async with factory() as s:
        s.add(row)
        await s.commit()
    try:
        yield row
    finally:
        async with factory() as s:
            await s.execute(delete(Contract).where(Contract.id == row.id))
            await s.commit()


@pytest.fixture(autouse=True)
def _restore_runtime() -> AsyncIterator[None]:
    original = intelligence_agent.RUNTIME
    engine.invalidate_cache()
    yield
    intelligence_agent.RUNTIME = original
    engine.invalidate_cache()


def _fake_analysis() -> OpenAIAgentsRuntime:
    payload = {
        "findings": [
            {"ok": True, "text": "Governing law present", "clause_title": ""},
            {"ok": False, "text": "No force majeure clause", "clause_title": ""},
        ],
        "missing_clauses": ["Force Majeure"],
        "clauses": [
            {
                "clause_title": "Payment",
                "risk": "medium",
                "risk_reason": "No late-payment interest specified",
                "summary": "Sets the payment amount and deadline.",
                "suggestions": [{"text": "Add late-payment interest", "rationale": "Common protection"}],
                "depends_on": [],
                "referenced_by": ["Termination"],
                "cross_references": [],
            },
            {
                "clause_title": "Termination",
                "risk": "low",
                "depends_on": ["Payment"],
            },
        ],
    }
    return OpenAIAgentsRuntime(FakeModel([Turn(output=[text_message(json.dumps(payload))])]))


async def test_intelligence_endpoint_returns_merged_analysis(client: AsyncClient, contract: Contract) -> None:
    intelligence_agent.RUNTIME = _fake_analysis()

    resp = await client.post(
        f"/api/v1/contracts/{contract.id}/intelligence",
        json={"document": DOC, "perspective": "neutral"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["cached"] is False
    assert body["missing_clauses"] == ["Force Majeure"]
    assert len(body["clauses"]) == 2
    payment = next(c for c in body["clauses"] if c["title"] == "Payment")
    assert payment["risk"] == "medium"
    assert payment["suggestions"][0]["text"] == "Add late-payment interest"
    assert payment["analytics"]["words"] > 0
    assert payment["analytics"]["variables"] == 1


async def test_intelligence_endpoint_serves_cache_on_repeat_call(
    client: AsyncClient, contract: Contract
) -> None:
    """Never analyzes the same document twice: a second call with an unchanged document
    + perspective returns the cached result instead of invoking the model again."""
    intelligence_agent.RUNTIME = _fake_analysis()

    first = await client.post(
        f"/api/v1/contracts/{contract.id}/intelligence",
        json={"document": DOC, "perspective": "neutral"},
    )
    assert first.json()["cached"] is False

    # Swap in a runtime that would error if called — proves the second call never
    # reaches the model.
    class _ExplodingRuntime:
        async def run(self, *a: object, **k: object) -> None:
            raise AssertionError("should not re-run the model for a cached document")

    intelligence_agent.RUNTIME = _ExplodingRuntime()  # type: ignore[assignment]

    second = await client.post(
        f"/api/v1/contracts/{contract.id}/intelligence",
        json={"document": DOC, "perspective": "neutral"},
    )
    assert second.status_code == 200
    assert second.json()["cached"] is True


async def test_intelligence_endpoint_404s_for_an_unknown_contract(client: AsyncClient) -> None:
    resp = await client.post(
        f"/api/v1/contracts/{uuid.uuid4()}/intelligence",
        json={"document": DOC},
    )
    assert resp.status_code == 404


async def test_intelligence_endpoint_rejects_an_empty_document(
    client: AsyncClient, contract: Contract
) -> None:
    resp = await client.post(
        f"/api/v1/contracts/{contract.id}/intelligence",
        json={"document": ""},
    )
    assert resp.status_code == 422


async def test_updating_variables_invalidates_the_intelligence_cache(
    client: AsyncClient, contract: Contract
) -> None:
    """Live updates after edits: a variable update must not leave a stale cached
    analysis (e.g. stale `variables_unresolved`) being served."""
    intelligence_agent.RUNTIME = _fake_analysis()
    await client.post(
        f"/api/v1/contracts/{contract.id}/intelligence",
        json={"document": DOC, "perspective": "neutral"},
    )
    key = engine._cache_key(DOC, "neutral")
    assert engine._cache_get(key) is not None

    await client.patch(
        f"/api/v1/contracts/{contract.id}/variables",
        json={"values": {"amount": "$500"}},
    )
    assert engine._cache_get(key) is None
