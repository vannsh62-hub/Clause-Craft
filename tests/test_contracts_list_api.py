"""`GET /contracts` — the list behind the sidebar history and projects."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from backend.api.deps import get_session_factory
from backend.api.main import app
from backend.workspace.models import Contract, ContractVersion


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_list_orders_newest_first_and_marks_finalized_as_projects(
    client: AsyncClient,
) -> None:
    factory = get_session_factory()
    older = Contract(id=uuid.uuid4(), contract_type="nda", request="older NDA")
    newer = Contract(id=uuid.uuid4(), contract_type="service", request="newer MSA")
    async with factory() as s:
        s.add(older)
        await s.commit()
        s.add(newer)
        await s.commit()
        # A finalized draft turns `newer` into a "project".
        s.add(
            ContractVersion(
                contract_id=newer.id,
                attempt=1,
                path="draft_v1.docx",
                markdown="x",
                finalized_at=datetime.now(timezone.utc),
            )
        )
        await s.commit()

    try:
        rows = (await client.get("/api/v1/contracts")).json()
        by_id = {r["id"]: r for r in rows}

        assert str(newer.id) in by_id and str(older.id) in by_id
        ids = [r["id"] for r in rows]
        assert ids.index(str(newer.id)) < ids.index(str(older.id)), "newest first"

        assert by_id[str(newer.id)]["finalized"] is True, "finalized draft => a project"
        assert by_id[str(older.id)]["finalized"] is False

        for r in rows:
            assert {"id", "request", "status", "created_at", "finalized"} <= set(r)
    finally:
        async with factory() as s:
            await s.execute(delete(Contract).where(Contract.id.in_([older.id, newer.id])))
            await s.commit()


async def test_the_list_is_capped(client: AsyncClient) -> None:
    """A history that grew without bound would drown the sidebar and the query."""
    rows = (await client.get("/api/v1/contracts")).json()
    assert len(rows) <= 50
