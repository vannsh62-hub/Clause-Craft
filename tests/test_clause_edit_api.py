"""`POST /contracts/{id}/clauses/edit/suggest` — the Edit popover's ✨ AI Assistant action.

Goes through the one-turn suggestion agent, so these tests wire a `FakeModel` the same
way `tests/test_clause_fill_details_api.py` drives `fill_agent`.
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
from backend.runtime.adapters.openai_agents import OpenAIAgentsRuntime
from backend.subagents.clause_edit import clause_edit_agent
from backend.workspace.models import Contract
from tests.fakes import FakeModel, Turn, text_message


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def contract() -> AsyncIterator[Contract]:
    factory = get_session_factory()
    row = Contract(
        id=uuid.uuid4(),
        contract_type="nda",
        request="an NDA",
        variables={"duration": "3"},
    )
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
    original = clause_edit_agent.RUNTIME
    yield
    clause_edit_agent.RUNTIME = original


def _fake(updated_clause: str, summary: str = "") -> OpenAIAgentsRuntime:
    payload = {"updated_clause": updated_clause, "summary": summary}
    return OpenAIAgentsRuntime(FakeModel([Turn(output=[text_message(json.dumps(payload))])]))


CLAUSE = "## 3. Confidentiality\n\nEach party shall keep the other's information confidential."


async def test_suggest_returns_the_agents_rewrite(client: AsyncClient, contract: Contract) -> None:
    rewritten = "## 3. Confidentiality\n\nEach party shall keep the other's information strictly confidential for five years."
    clause_edit_agent.RUNTIME = _fake(rewritten, summary="Added a five-year term.")

    resp = await client.post(
        f"/api/v1/contracts/{contract.id}/clauses/edit/suggest",
        json={"clause_markdown": CLAUSE, "instruction": "Add a five-year term"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["updated_clause"] == rewritten
    assert body["summary"] == "Added a five-year term."


async def test_suggest_404s_for_an_unknown_contract(client: AsyncClient) -> None:
    resp = await client.post(
        f"/api/v1/contracts/{uuid.uuid4()}/clauses/edit/suggest",
        json={"clause_markdown": CLAUSE, "instruction": "Add a term"},
    )
    assert resp.status_code == 404


async def test_suggest_rejects_empty_instruction(client: AsyncClient, contract: Contract) -> None:
    resp = await client.post(
        f"/api/v1/contracts/{contract.id}/clauses/edit/suggest",
        json={"clause_markdown": CLAUSE, "instruction": ""},
    )
    assert resp.status_code == 422


async def test_suggest_rejects_empty_clause_markdown(client: AsyncClient, contract: Contract) -> None:
    resp = await client.post(
        f"/api/v1/contracts/{contract.id}/clauses/edit/suggest",
        json={"clause_markdown": "", "instruction": "Add a term"},
    )
    assert resp.status_code == 422


async def test_suggest_never_mutates_the_contract(client: AsyncClient, contract: Contract) -> None:
    """The endpoint only returns a suggestion — it must not write to the contract's
    stored variables or otherwise persist anything."""
    clause_edit_agent.RUNTIME = _fake("## 3. Confidentiality\n\nRewritten text.")

    resp = await client.post(
        f"/api/v1/contracts/{contract.id}/clauses/edit/suggest",
        json={"clause_markdown": CLAUSE, "instruction": "Rewrite this"},
    )
    assert resp.status_code == 200

    factory = get_session_factory()
    async with factory() as s:
        row = await s.get(Contract, contract.id)
        assert row is not None
        assert row.variables == {"duration": "3"}
