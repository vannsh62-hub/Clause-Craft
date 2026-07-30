"""`POST /contracts/{id}/clauses/assistant` — the chat panel that proposes clause edits.

Structured-output only; these tests don't script a `list_clause_library` tool call (see
`tests/test_reference_provider.py` for that pattern) since the agent's shape under test is
its final proposal, not tool use.
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
from backend.subagents.clause_actions import clause_actions_agent
from backend.workspace.models import Contract
from tests.fakes import FakeModel, Turn, text_message

DOC = "# NDA\n\n## 1. Confidentiality\n\nThe receiving party shall keep it secret.\n"


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def contract() -> AsyncIterator[Contract]:
    factory = get_session_factory()
    row = Contract(id=uuid.uuid4(), contract_type="nda", request="an NDA")
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
    original = clause_actions_agent.RUNTIME
    yield
    clause_actions_agent.RUNTIME = original


def _fake(reply: str, actions: list[dict[str, object]]) -> OpenAIAgentsRuntime:
    payload = {"reply": reply, "actions": actions}
    return OpenAIAgentsRuntime(FakeModel([Turn(output=[text_message(json.dumps(payload))])]))


async def test_proposes_an_insert_action(client: AsyncClient, contract: Contract) -> None:
    clause_actions_agent.RUNTIME = _fake(
        "I'll add a governing law clause at the end.",
        [
            {
                "action": "insert",
                "clause_id": "nda.governing_law",
                "after_clause_title": "Confidentiality",
                "reason": "requested clause not yet present",
            }
        ],
    )

    resp = await client.post(
        f"/api/v1/contracts/{contract.id}/clauses/assistant",
        json={"message": "add a governing law clause", "document": DOC},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "governing law" in body["reply"].lower()
    assert body["actions"] == [
        {
            "action": "insert",
            "clause_title": "",
            "clause_id": "nda.governing_law",
            "after_clause_title": "Confidentiality",
            "fields": {},
            "reason": "requested clause not yet present",
        }
    ]


async def test_a_question_with_no_document_change_returns_no_actions(
    client: AsyncClient, contract: Contract
) -> None:
    clause_actions_agent.RUNTIME = _fake("This NDA has one clause: Confidentiality.", [])

    resp = await client.post(
        f"/api/v1/contracts/{contract.id}/clauses/assistant",
        json={"message": "what clauses does this have?", "document": DOC},
    )
    assert resp.status_code == 200
    assert resp.json()["actions"] == []


async def test_an_unrecognised_action_kind_is_dropped(client: AsyncClient, contract: Contract) -> None:
    """Defence in depth: only the four known action kinds ever reach the client, even if
    the model returns something else."""
    clause_actions_agent.RUNTIME = _fake(
        "done",
        [
            {"action": "rewrite_everything", "reason": "not a real action"},
            {"action": "remove", "clause_title": "Confidentiality", "reason": "duplicate"},
        ],
    )

    resp = await client.post(
        f"/api/v1/contracts/{contract.id}/clauses/assistant",
        json={"message": "remove confidentiality", "document": DOC},
    )
    assert resp.status_code == 200
    actions = resp.json()["actions"]
    assert len(actions) == 1
    assert actions[0]["action"] == "remove"
    assert actions[0]["clause_title"] == "Confidentiality"


async def test_fill_action_carries_field_values(client: AsyncClient, contract: Contract) -> None:
    clause_actions_agent.RUNTIME = _fake(
        "Set the notice period to 30 days.",
        [
            {
                "action": "fill",
                "clause_title": "Confidentiality",
                "fields": [{"name": "notice_days", "value": "30"}],
                "reason": "user specified 30 days",
            }
        ],
    )

    resp = await client.post(
        f"/api/v1/contracts/{contract.id}/clauses/assistant",
        json={"message": "set notice period to 30 days", "document": DOC},
    )
    assert resp.status_code == 200
    assert resp.json()["actions"][0]["fields"] == {"notice_days": "30"}


async def test_404s_for_an_unknown_contract(client: AsyncClient) -> None:
    resp = await client.post(
        f"/api/v1/contracts/{uuid.uuid4()}/clauses/assistant",
        json={"message": "hello", "document": DOC},
    )
    assert resp.status_code == 404


async def test_rejects_an_empty_message(client: AsyncClient, contract: Contract) -> None:
    resp = await client.post(
        f"/api/v1/contracts/{contract.id}/clauses/assistant",
        json={"message": "", "document": DOC},
    )
    assert resp.status_code == 422
