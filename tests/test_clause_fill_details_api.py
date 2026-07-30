"""`POST /contracts/{id}/clauses/analyse` and `/fill` — the Fill-details modal's backend.

`analyse` is deterministic and covered without any fake model. `fill` goes through the
one-turn suggestion agent, so those tests wire a `FakeModel` the same way the rest of the
suite drives spec-driven agents (see `tests/fakes.py`).
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
from backend.subagents.fill_details import fill_agent
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
        variables={"duration": "3", "service_provider": "ABC Pvt Ltd"},
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


# ------------------------------------------------------------------------ analyse


async def test_analyse_resolves_known_fields_through_the_alias_table(
    client: AsyncClient, contract: Contract
) -> None:
    """`duration` is stored under a near-miss name; the alias table maps it to
    `duration_years`, same as `/render` self-heals it."""
    resp = await client.post(
        f"/api/v1/contracts/{contract.id}/clauses/analyse",
        json={"fields": ["duration_years", "service_provider", "governing_law"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["known"] == {"duration_years": "3", "service_provider": "ABC Pvt Ltd"}
    assert body["missing"] == ["governing_law"]


async def test_analyse_404s_for_an_unknown_contract(client: AsyncClient) -> None:
    resp = await client.post(
        f"/api/v1/contracts/{uuid.uuid4()}/clauses/analyse",
        json={"fields": ["duration_years"]},
    )
    assert resp.status_code == 404


async def test_analyse_rejects_an_empty_field_list(client: AsyncClient, contract: Contract) -> None:
    resp = await client.post(
        f"/api/v1/contracts/{contract.id}/clauses/analyse",
        json={"fields": []},
    )
    assert resp.status_code == 422


# ------------------------------------------------------------------------ fill


@pytest.fixture(autouse=True)
def _restore_runtime() -> AsyncIterator[None]:
    original = fill_agent.RUNTIME
    yield
    fill_agent.RUNTIME = original


def _fake(suggestions: dict[str, str], unresolved: list[str] | None = None) -> OpenAIAgentsRuntime:
    payload = {
        "suggestions": [{"name": k, "value": v} for k, v in suggestions.items()],
        "unresolved": unresolved or [],
    }
    return OpenAIAgentsRuntime(FakeModel([Turn(output=[text_message(json.dumps(payload))])]))


async def test_fill_returns_the_agents_suggestions(client: AsyncClient, contract: Contract) -> None:
    fill_agent.RUNTIME = _fake({"notice_days": "30"})

    resp = await client.post(
        f"/api/v1/contracts/{contract.id}/clauses/fill",
        json={"clause_text": "Notice period: {{ notice_days }} days.", "fields": ["notice_days"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["suggestions"] == {"notice_days": "30"}
    assert body["unresolved"] == []


async def test_fill_reports_fields_the_agent_declined_to_guess(
    client: AsyncClient, contract: Contract
) -> None:
    fill_agent.RUNTIME = _fake({}, unresolved=["signatory_name"])

    resp = await client.post(
        f"/api/v1/contracts/{contract.id}/clauses/fill",
        json={"clause_text": "Signed by: {{ signatory_name }}.", "fields": ["signatory_name"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["suggestions"] == {}
    assert body["unresolved"] == ["signatory_name"]


async def test_fill_ignores_a_field_the_agent_was_not_asked_about(
    client: AsyncClient, contract: Contract
) -> None:
    """Defence in depth: even if the model returns an extra name, only requested fields
    can come back — the endpoint must not surface an invented field to the modal."""
    fill_agent.RUNTIME = _fake({"notice_days": "30", "unasked_field": "x"})

    resp = await client.post(
        f"/api/v1/contracts/{contract.id}/clauses/fill",
        json={"clause_text": "Notice period: {{ notice_days }} days.", "fields": ["notice_days"]},
    )
    assert resp.status_code == 200
    assert resp.json()["suggestions"] == {"notice_days": "30"}


async def test_fill_404s_for_an_unknown_contract(client: AsyncClient) -> None:
    resp = await client.post(
        f"/api/v1/contracts/{uuid.uuid4()}/clauses/fill",
        json={"clause_text": "x {{ y }}", "fields": ["y"]},
    )
    assert resp.status_code == 404


async def test_fill_rejects_too_many_fields(client: AsyncClient, contract: Contract) -> None:
    resp = await client.post(
        f"/api/v1/contracts/{contract.id}/clauses/fill",
        json={"clause_text": "x", "fields": [f"f{i}" for i in range(51)]},
    )
    assert resp.status_code == 422
