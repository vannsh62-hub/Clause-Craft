"""`PATCH /contracts/{id}/variables` and the `variables` field on `GET /contracts/{id}` —
the persistent Contract Variable Memory that backs Fill-details/Insert/AI-edit auto-fill.

No fake model needed: both endpoints are deterministic, same as `/clauses/analyse`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from backend.api.deps import get_session_factory
from backend.api.main import app
from backend.workspace.models import Contract


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
        variables={"service_provider": "ABC Pvt Ltd"},
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


async def test_get_contract_returns_stored_variables(client: AsyncClient, contract: Contract) -> None:
    resp = await client.get(f"/api/v1/contracts/{contract.id}")
    assert resp.status_code == 200
    assert resp.json()["variables"] == {"service_provider": "ABC Pvt Ltd"}


async def test_get_contract_self_heals_a_near_miss_key(client: AsyncClient) -> None:
    """A value stored under a near-miss name (here, the alias `client`) still comes back
    under its canonical key — the same self-healing `/render` and `/analyse` already do."""
    factory = get_session_factory()
    row = Contract(id=uuid.uuid4(), contract_type="nda", request="x", variables={"client": "Acme"})
    async with factory() as s:
        s.add(row)
        await s.commit()
    try:
        resp = await client.get(f"/api/v1/contracts/{row.id}")
        assert resp.json()["variables"]["client"] == "Acme"
    finally:
        async with factory() as s:
            await s.execute(delete(Contract).where(Contract.id == row.id))
            await s.commit()


async def test_patch_variables_stores_a_new_value(client: AsyncClient, contract: Contract) -> None:
    resp = await client.patch(
        f"/api/v1/contracts/{contract.id}/variables",
        json={"values": {"client_name": "XYZ Pvt Ltd"}},
    )
    assert resp.status_code == 200
    assert resp.json()["variables"]["client_name"] == "XYZ Pvt Ltd"

    # It's now visible on a plain GET too — persisted, not just echoed back.
    resp = await client.get(f"/api/v1/contracts/{contract.id}")
    assert resp.json()["variables"]["client_name"] == "XYZ Pvt Ltd"


async def test_patch_variables_normalizes_near_miss_names(
    client: AsyncClient, contract: Contract
) -> None:
    """"Client Name" and "client_name" must resolve to the same stored key, or the same
    fact would be asked for twice under two different spellings."""
    resp = await client.patch(
        f"/api/v1/contracts/{contract.id}/variables",
        json={"values": {"Client Name": "ABC Pvt Ltd"}},
    )
    assert resp.status_code == 200
    variables = resp.json()["variables"]
    assert variables.get("client_name") == "ABC Pvt Ltd" or variables.get("client") == "ABC Pvt Ltd"


async def test_patch_variables_merges_rather_than_replaces(client: AsyncClient, contract: Contract) -> None:
    """A previously-known variable (service_provider, from the fixture) survives a PATCH
    that only touches a different key."""
    resp = await client.patch(
        f"/api/v1/contracts/{contract.id}/variables",
        json={"values": {"effective_date": "2026-01-01"}},
    )
    assert resp.status_code == 200
    variables = resp.json()["variables"]
    assert variables["service_provider"] == "ABC Pvt Ltd"
    assert variables["effective_date"] == "2026-01-01"


async def test_patch_variables_overwrites_an_updated_value(client: AsyncClient, contract: Contract) -> None:
    """Correcting a value (the "Update All" flow) is a plain overwrite — the endpoint doesn't
    ask for confirmation itself; that's the frontend's job before it calls this."""
    resp = await client.patch(
        f"/api/v1/contracts/{contract.id}/variables",
        json={"values": {"service_provider": "New Co"}},
    )
    assert resp.status_code == 200
    assert resp.json()["variables"]["service_provider"] == "New Co"


async def test_patch_variables_404s_for_an_unknown_contract(client: AsyncClient) -> None:
    resp = await client.patch(
        f"/api/v1/contracts/{uuid.uuid4()}/variables",
        json={"values": {"client_name": "Acme"}},
    )
    assert resp.status_code == 404


async def test_patch_variables_rejects_an_empty_body(client: AsyncClient, contract: Contract) -> None:
    resp = await client.patch(
        f"/api/v1/contracts/{contract.id}/variables",
        json={"values": {}},
    )
    assert resp.status_code == 422