"""`/playbook/rules` — the structured, table-friendly view of the playbook.

The rules power a table in the UI and are edited one at a time. Every write reserialises the
file and validates it through the same loader the pipeline uses, so a rule that would break a
run is refused. The playbook file is restored after each test.
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
def _restore() -> Iterator[None]:
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


def _rule(**over: object) -> dict:
    base = {
        "id": "probe-rule",
        "when": [{"field": "industry", "op": "eq", "value": "software"}],
        "kind": "set_value",
        "target": "payment_terms_days",
        "value": "45",
        "reason": "probe",
        "blocking": True,
    }
    base.update(over)
    return base


async def test_rules_come_back_as_structured_rows_with_an_explanation(client: AsyncClient) -> None:
    view = (await client.get("/api/v1/playbook/rules")).json()
    assert view["explanation"], "the UI shows a plain-English explanation of what a playbook is"
    assert view["rules"]
    row = view["rules"][0]
    assert {"id", "kind", "target"} <= set(row)


async def test_add_a_rule(client: AsyncClient) -> None:
    response = await client.post("/api/v1/playbook/rules", json=_rule())
    assert response.status_code == 201
    ids = {r["id"] for r in response.json()["rules"]}
    assert "probe-rule" in ids

    # And it is loadable by the pipeline (the write cleared the loader cache).
    assert any(r.id == "probe-rule" for r in load_playbook("default"))


async def test_adding_a_duplicate_id_is_refused(client: AsyncClient) -> None:
    await client.post("/api/v1/playbook/rules", json=_rule())
    again = await client.post("/api/v1/playbook/rules", json=_rule())
    assert again.status_code == 409


async def test_edit_a_rule(client: AsyncClient) -> None:
    await client.post("/api/v1/playbook/rules", json=_rule())
    edited = await client.put(
        "/api/v1/playbook/rules/probe-rule", json=_rule(value="60", reason="changed")
    )
    assert edited.status_code == 200
    row = next(r for r in edited.json()["rules"] if r["id"] == "probe-rule")
    assert row["value"] == "60"
    assert row["reason"] == "changed"


async def test_editing_an_unknown_rule_is_404(client: AsyncClient) -> None:
    response = await client.put("/api/v1/playbook/rules/nope", json=_rule(id="nope"))
    assert response.status_code == 404


async def test_remove_a_rule(client: AsyncClient) -> None:
    await client.post("/api/v1/playbook/rules", json=_rule())
    removed = await client.delete("/api/v1/playbook/rules/probe-rule")
    assert removed.status_code == 200
    assert "probe-rule" not in {r["id"] for r in removed.json()["rules"]}


async def test_removing_an_unknown_rule_is_404(client: AsyncClient) -> None:
    assert (await client.delete("/api/v1/playbook/rules/nope")).status_code == 404


async def test_a_rule_carrying_clause_text_is_refused(client: AsyncClient) -> None:
    """The playbook holds conditions, not language — enforced on structured writes too."""
    smuggled = _rule(
        kind="set_value",
        target="confidentiality",
        value=(
            "The Receiving Party shall hold all Confidential Information in strict confidence "
            "and shall not disclose it to any third party without prior written consent."
        ),
    )
    response = await client.post("/api/v1/playbook/rules", json=smuggled)
    assert response.status_code == 422
    assert "clause text" in response.json()["detail"]


async def test_a_rule_with_no_conditions_always_applies(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/playbook/rules",
        json=_rule(
            id="always", when=[], kind="require_section", target="governing_law", value=None
        ),
    )
    assert response.status_code == 201
    row = next(r for r in response.json()["rules"] if r["id"] == "always")
    assert row["when"] == []
