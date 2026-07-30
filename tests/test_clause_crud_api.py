"""Creating, editing, and removing clauses through the API.

The library is the source of truth for drafting, so every write is validated by the same
loader the pipeline uses — a clause that would not load is refused. These tests exercise
that, and clean up any clause they create so the on-disk library is left as it was.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend.api.main import app
from backend.clauselib.loader import CLAUSE_ROOT, load_library

TEST_ID = "nda.api_crud_probe"
TEST_PATH = CLAUSE_ROOT / "nda" / "api_crud_probe.md"


@pytest.fixture(autouse=True)
def _cleanup() -> Iterator[None]:
    """Remove any file the tests created, and refresh the loader cache."""
    try:
        yield
    finally:
        TEST_PATH.unlink(missing_ok=True)
        TEST_PATH.with_suffix(".md.tmp").unlink(missing_ok=True)
        load_library.cache_clear()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _payload(**over: object) -> dict:
    base = {
        "id": TEST_ID,
        "title": "Probe",
        "contract_type": "nda",
        "country": "IN",
        "required": False,
        "order": 95,
        "risk": "high",
        "body": "{{ receiving_party }} agrees to the probe clause.",
    }
    base.update(over)
    return base


# ------------------------------------------------------------------------ the view


async def test_list_carries_the_library_columns(client: AsyncClient) -> None:
    """The table view needs category, country, risk, and status per clause."""
    rows = (await client.get("/api/v1/clauses")).json()
    assert rows
    row = rows[0]
    for field in ("id", "category", "contract_type", "country", "version", "risk", "status"):
        assert field in row
    assert row["status"] == "Approved"


# --------------------------------------------------------------------------- create


async def test_create_then_it_appears_in_the_library(client: AsyncClient) -> None:
    response = await client.post("/api/v1/clauses", json=_payload())

    assert response.status_code == 201
    created = response.json()
    assert created["id"] == TEST_ID
    assert created["risk"] == "high"
    assert created["version"] == 1
    # Variables were derived from the body, not supplied.
    assert created["variables"] == ["receiving_party"]

    listed = {c["id"] for c in (await client.get("/api/v1/clauses")).json()}
    assert TEST_ID in listed


async def test_creating_a_duplicate_is_rejected(client: AsyncClient) -> None:
    await client.post("/api/v1/clauses", json=_payload())
    again = await client.post("/api/v1/clauses", json=_payload())
    assert again.status_code == 409


async def test_a_body_that_will_not_load_is_refused(client: AsyncClient) -> None:
    """The loader validates; an empty body has nothing to render."""
    response = await client.post("/api/v1/clauses", json=_payload(body="   "))
    assert response.status_code == 422


async def test_a_placeholder_with_a_space_is_a_readable_422_not_a_500(
    client: AsyncClient,
) -> None:
    """`{{Vendor Name}}` is what a person writes, and it is not valid Jinja.

    The body is parsed as a template to derive its variables, so the syntax error used to
    escape the request handler as a 500 — the UI showed a bare "500 Internal Server Error"
    with no clue that a placeholder was at fault.
    """
    response = await client.post(
        "/api/v1/clauses", json=_payload(body="{{Vendor Name}} shall deliver the Services.")
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "placeholder" in detail
    assert "{{ vendor_name }}" in detail, "the message must show the correct form"


async def test_an_id_not_matching_its_contract_type_is_refused(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/clauses", json=_payload(id="service.api_crud_probe", contract_type="nda")
    )
    assert response.status_code == 422


# ----------------------------------------------------------------------------- edit


async def test_edit_bumps_the_version_and_rederives_variables(client: AsyncClient) -> None:
    await client.post("/api/v1/clauses", json=_payload())

    edited = await client.put(
        f"/api/v1/clauses/{TEST_ID}",
        json=_payload(
            title="Probe Edited",
            risk="low",
            body="{{ receiving_party }} and {{ disclosing_party }} agree.",
        ),
    )

    assert edited.status_code == 200
    result = edited.json()
    assert result["version"] == 2
    assert result["risk"] == "low"
    assert result["variables"] == ["disclosing_party", "receiving_party"]


async def test_editing_an_unknown_clause_is_404(client: AsyncClient) -> None:
    response = await client.put(f"/api/v1/clauses/{TEST_ID}", json=_payload())
    assert response.status_code == 404


async def test_the_id_cannot_be_changed_by_editing(client: AsyncClient) -> None:
    await client.post("/api/v1/clauses", json=_payload())
    response = await client.put(
        f"/api/v1/clauses/{TEST_ID}", json=_payload(id="nda.something_else")
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- delete


async def test_delete_removes_it_from_the_library(client: AsyncClient) -> None:
    await client.post("/api/v1/clauses", json=_payload())

    deleted = await client.delete(f"/api/v1/clauses/{TEST_ID}")
    assert deleted.status_code == 204

    listed = {c["id"] for c in (await client.get("/api/v1/clauses")).json()}
    assert TEST_ID not in listed


async def test_deleting_an_unknown_clause_is_404(client: AsyncClient) -> None:
    response = await client.delete(f"/api/v1/clauses/{TEST_ID}")
    assert response.status_code == 404


async def test_a_created_clause_is_usable_by_the_pipeline(client: AsyncClient) -> None:
    """A clause added through the API loads through the same path the drafting engine reads,
    so it is immediately a real, draftable clause — not a second-class UI-only entry."""
    await client.post("/api/v1/clauses", json=_payload())

    from backend.clauselib.loader import get_clause

    clause = get_clause(TEST_ID)
    assert clause.risk == "high"
    assert "receiving_party" in clause.variables
