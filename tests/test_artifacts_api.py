"""`GET /contracts/{id}/artifacts` — the typed artifact catalogue.

A UI showing "Intent ✓, CKO ✓, Transformation Plan ✓" needs to know which named artifacts
exist. The raw workspace listing gives paths; this gives the named catalogue, with a
`present` flag per artifact, which the one-canonical-path-per-artifact layout makes possible.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.api.deps import get_session_factory
from backend.api.main import app
from backend.artifacts import Artifact, ArtifactStore
from backend.schemas.intent import IntentObject
from backend.workspace.models import Contract


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def stored_contract() -> AsyncIterator[tuple[uuid.UUID, async_sessionmaker[AsyncSession]]]:
    factory = get_session_factory()
    contract = Contract(id=uuid.uuid4(), contract_type="nda", request="Draft an NDA")
    async with factory() as s:
        s.add(contract)
        await s.commit()
    try:
        yield contract.id, factory
    finally:
        async with factory() as s:
            await s.execute(delete(Contract).where(Contract.id == contract.id))
            await s.commit()


async def test_the_catalogue_lists_every_artifact(
    client: AsyncClient, stored_contract: tuple[uuid.UUID, async_sessionmaker[AsyncSession]]
) -> None:
    contract_id, _ = stored_contract

    response = await client.get(f"/api/v1/contracts/{contract_id}/artifacts")

    assert response.status_code == 200
    catalogue = response.json()
    names = {row["name"] for row in catalogue}
    assert {"INTENT", "CKO", "TRANSFORMATION_PLAN"} <= names
    assert all(row["present"] is False for row in catalogue), "nothing produced yet"


async def test_a_produced_artifact_shows_present(
    client: AsyncClient, stored_contract: tuple[uuid.UUID, async_sessionmaker[AsyncSession]]
) -> None:
    contract_id, factory = stored_contract
    await ArtifactStore(factory, contract_id).save(
        Artifact.INTENT, IntentObject(contract_type="nda", confidence=0.9)
    )

    response = await client.get(f"/api/v1/contracts/{contract_id}/artifacts")

    catalogue = {row["name"]: row for row in response.json()}
    assert catalogue["INTENT"]["present"] is True
    assert catalogue["INTENT"]["path"] == "work/00-intent.json"
    assert catalogue["CKO"]["present"] is False


async def test_an_unknown_contract_is_404(client: AsyncClient) -> None:
    response = await client.get(f"/api/v1/contracts/{uuid.uuid4()}/artifacts")
    assert response.status_code == 404
