"""`GET/PUT /playbook` — viewing and editing the business rules from the UI.

Saving validates through the same loader the pipeline uses, so the endpoint refuses a
playbook that will not parse or that carries clause text. Those refusals are the point: a
playbook the UI could break silently is a compliance gap waiting to happen.

The tests restore the original playbook file afterwards, since editing writes it on disk.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend.api.main import app
from backend.knowledge.providers.playbook import PLAYBOOK_DIR, load_playbook

PLAYBOOK = PLAYBOOK_DIR / "default.yaml"


@pytest.fixture(autouse=True)
def _restore_playbook() -> Iterator[None]:
    """Editing writes the real file; put it back exactly as it was."""
    original = PLAYBOOK.read_text(encoding="utf-8")
    try:
        yield
    finally:
        PLAYBOOK.write_text(original, encoding="utf-8")
        load_playbook.cache_clear()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_get_returns_the_yaml_and_a_rule_count(client: AsyncClient) -> None:
    response = await client.get("/api/v1/playbook")

    assert response.status_code == 200
    body = response.json()
    assert "rules:" in body["yaml"]
    assert body["rule_count"] >= 1


async def test_a_valid_edit_is_saved(client: AsyncClient) -> None:
    edited = (
        "rules:\n"
        "  - id: only-rule\n"
        "    when: []\n"
        "    kind: require_section\n"
        "    target: governing_law\n"
        "    reason: every contract states its governing law\n"
    )

    response = await client.put("/api/v1/playbook", json={"yaml": edited})

    assert response.status_code == 200
    assert response.json()["rule_count"] == 1
    # And it round-trips.
    reread = await client.get("/api/v1/playbook")
    assert "only-rule" in reread.json()["yaml"]


async def test_a_playbook_carrying_clause_text_is_refused(client: AsyncClient) -> None:
    """The important refusal: a rule states a condition; language belongs in the library."""
    smuggled = (
        "rules:\n"
        "  - id: bad\n"
        "    kind: set_value\n"
        "    target: confidentiality\n"
        "    value: >\n"
        "      The Receiving Party shall hold all Confidential Information in strict confidence "
        "and shall not disclose it to any third party without prior written consent.\n"
    )

    response = await client.put("/api/v1/playbook", json={"yaml": smuggled})

    assert response.status_code == 422
    assert "clause text" in response.json()["detail"]


async def test_malformed_yaml_is_refused(client: AsyncClient) -> None:
    response = await client.put("/api/v1/playbook", json={"yaml": "rules: [unclosed"})
    assert response.status_code == 422


async def test_a_document_without_rules_is_refused(client: AsyncClient) -> None:
    response = await client.put("/api/v1/playbook", json={"yaml": "something_else: true"})
    assert response.status_code == 422
    assert "rules" in response.json()["detail"]


async def test_a_rejected_edit_does_not_change_the_file(client: AsyncClient) -> None:
    """A refused save must leave the working playbook intact, not half-written."""
    before = (await client.get("/api/v1/playbook")).json()["yaml"]

    await client.put("/api/v1/playbook", json={"yaml": "rules: [unclosed"})

    after = (await client.get("/api/v1/playbook")).json()["yaml"]
    assert after == before
