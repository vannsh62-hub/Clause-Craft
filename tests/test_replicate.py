"""Several independent drafts from one uploaded document.

"Use this NDA as a template and give me three drafts I can adapt." Every copy must start from
*the same bytes* — a replica that was re-encoded on the way in is not a replica, and the
fidelity guarantee the template path makes would be measured against the wrong original.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from backend.api.deps import get_session_factory
from backend.api.main import app
from backend.knowledge.providers.template import POINTER_PATH
from backend.storage import get_storage
from backend.workspace.models import Contract
from backend.workspace.store import WorkspaceStore
from tests.pipeline_fakes import wire_pipeline

FIXTURE = Path(__file__).parent / "data" / "sla-sample.docx"
DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def cleanup() -> AsyncIterator[list[uuid.UUID]]:
    created: list[uuid.UUID] = []
    try:
        yield created
    finally:
        factory = get_session_factory()
        for cid in created:
            async with factory() as s:
                await s.execute(delete(Contract).where(Contract.id == cid))
                await s.commit()


async def _replicate(client: AsyncClient, copies: int) -> dict:
    return (
        await client.post(
            "/api/v1/contracts/replicate",
            data={"request": "Adapt this into a reseller agreement.", "copies": str(copies)},
            files={"files": ("sla-sample.docx", FIXTURE.read_bytes(), DOCX_MEDIA)},
        )
    ).json()


async def test_three_copies_become_three_contracts(
    client: AsyncClient, cleanup: list[uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    wire_pipeline(monkeypatch, confidences=(0.3, 0.3, 0.3))

    body = await _replicate(client, 3)
    ids = [uuid.UUID(c["contract_id"]) for c in body["contracts"]]
    cleanup.extend(ids)

    assert len(ids) == 3
    assert len(set(ids)) == 3, "each copy is its own contract, not the same one three times"
    for entry in body["contracts"]:
        assert entry["events"] == f"/api/v1/runs/{entry['run_id']}/events"


async def test_every_copy_stores_byte_identical_source(
    client: AsyncClient, cleanup: list[uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-encoding on the way in would break the fidelity promise silently."""
    wire_pipeline(monkeypatch, confidences=(0.3, 0.3))
    original = FIXTURE.read_bytes()

    body = await _replicate(client, 2)
    ids = [uuid.UUID(c["contract_id"]) for c in body["contracts"]]
    cleanup.extend(ids)

    storage = get_storage()
    factory = get_session_factory()
    stored: list[bytes] = []
    for contract_id in ids:
        async with factory() as session:
            pointer = json.loads(await WorkspaceStore(session).read(contract_id, POINTER_PATH))
        stored.append(storage.get(pointer["storage_key"]))

    assert stored[0] == original
    assert stored[0] == stored[1]


async def test_a_non_docx_upload_is_refused(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    wire_pipeline(monkeypatch, confidences=(0.3,))
    response = await client.post(
        "/api/v1/contracts/replicate",
        data={"request": "Adapt this.", "copies": "1"},
        files={"files": ("notes.txt", b"not a document", "text/plain")},
    )
    assert response.status_code == 415


async def test_an_absurd_number_of_copies_is_refused(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    wire_pipeline(monkeypatch, confidences=(0.3,))
    response = await client.post(
        "/api/v1/contracts/replicate",
        data={"request": "Adapt this.", "copies": "50"},
        files={"files": ("sla-sample.docx", FIXTURE.read_bytes(), DOCX_MEDIA)},
    )
    assert response.status_code == 422


async def test_a_replicate_with_no_request_is_refused(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Copies with no brief would start runs with nothing to do."""
    wire_pipeline(monkeypatch, confidences=(0.3,))
    response = await client.post(
        "/api/v1/contracts/replicate",
        data={"request": "  ", "copies": "1"},
        files={"files": ("sla-sample.docx", FIXTURE.read_bytes(), DOCX_MEDIA)},
    )
    assert response.status_code == 422
