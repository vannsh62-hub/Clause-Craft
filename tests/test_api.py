"""The HTTP surface, driven through the real ASGI app with a fake model.

The background run task is real: `POST /contracts` returns `202` and an orchestrator starts
in the event loop. That is what these tests exercise, because a run that spends money must not
be tied to an HTTP connection that a proxy timeout can drop.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from agents.extensions.memory import SQLAlchemySession
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from backend.api.deps import get_engine, get_session_factory
from backend.api.main import app
from backend.api.routers.exports import DOCX_MEDIA
from backend.workspace.models import Contract, ContractVersion, Run
from tests.helpers import faithful_markdown, render_clauses_for, tool_ctx
from tests.pipeline_fakes import wire_pipeline


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
        # `try/finally`: pytest throws a failing test's exception into the generator at the
        # yield, so cleanup written after it is skipped precisely when a test has failed —
        # and a failing test is the one most likely to have left rows behind.
        factory = get_session_factory()
        for cid in created:
            async with factory() as s:
                await s.execute(delete(Contract).where(Contract.id == cid))
                await s.commit()
            await SQLAlchemySession(
                str(cid), engine=get_engine(), create_tables=True
            ).clear_session()


async def _wait_for_run_status(run_id: uuid.UUID, status: str, timeout: float = 10.0) -> Run:
    """Wait for the *run* row, not the contract row.

    These finish in a defined order and it is not the intuitive one: `_run_slice` marks the
    contract `ready`, and only then does `_drive` mark the run `complete`. Waiting on the
    contract and immediately asserting on the run is therefore a race — a narrow one, which
    is worse, because it passes locally and fails once every dozen CI runs.
    """
    factory = get_session_factory()
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        async with factory() as s:
            run = (await s.execute(select(Run).where(Run.id == run_id))).scalar_one()
            if run.status == status:
                return run
        await asyncio.sleep(0.05)
    raise AssertionError(f"run never reached status {status!r}")


async def _wait_for_status(contract_id: uuid.UUID, status: str, timeout: float = 10.0) -> None:
    factory = get_session_factory()
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        async with factory() as s:
            contract = await s.get(Contract, contract_id)
            if contract is not None and contract.status == status:
                return
        await asyncio.sleep(0.05)
    raise AssertionError(f"contract never reached status {status!r}")


# ------------------------------------------------------------------------------ basics


async def test_health(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_the_clause_library_is_browsable(client: AsyncClient) -> None:
    response = await client.get("/api/v1/clauses")
    assert response.status_code == 200

    clauses = response.json()
    # Subset, not equality: the library is editable through the clause CRUD API, so this
    # asserts the shipped baseline is browsable rather than pinning an exact count that a
    # UI-added clause would break.
    assert len(clauses) >= 13
    assert {"nda", "service"} <= {c["contract_type"] for c in clauses}


async def test_clauses_can_be_filtered_by_contract_type(client: AsyncClient) -> None:
    response = await client.get("/api/v1/clauses", params={"contract_type": "nda"})
    assert [c["id"] for c in response.json()][:2] == ["nda.definitions", "nda.confidentiality"]


async def test_an_unsupported_contract_type_is_404_and_says_what_is_supported(
    client: AsyncClient,
) -> None:
    response = await client.get("/api/v1/clauses", params={"contract_type": "employment"})
    assert response.status_code == 404
    assert "nda" in response.json()["detail"]


async def test_clause_text_exposes_the_approved_template(client: AsyncClient) -> None:
    response = await client.get("/api/v1/clauses/nda.confidentiality/text")
    body = response.json()

    assert body["version"] == 1
    assert "{{ receiving_party }}" in body["body"], "the raw template, unsubstituted"
    assert "receiving_party" in body["variables"]


async def test_unknown_clause_is_404(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/clauses/nda.nope/text")).status_code == 404


async def test_unknown_contract_is_404(client: AsyncClient) -> None:
    assert (await client.get(f"/api/v1/contracts/{uuid.uuid4()}")).status_code == 404


async def test_unknown_run_is_404(client: AsyncClient) -> None:
    assert (await client.get(f"/api/v1/runs/{uuid.uuid4()}/events")).status_code == 404


# --------------------------------------------------------------- the run, start to suspend


async def test_posting_a_request_returns_202_and_the_run_asks_a_question(
    client: AsyncClient, cleanup: list[uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    wire_pipeline(monkeypatch, confidences=(0.3,))  # low confidence → the run asks

    response = await client.post(
        "/api/v1/contracts", json={"request": "Draft an NDA between ABC and XYZ"}
    )
    assert response.status_code == 202

    body = response.json()
    contract_id = uuid.UUID(body["contract_id"])
    cleanup.append(contract_id)
    assert body["events"] == f"/api/v1/runs/{body['run_id']}/events"

    await _wait_for_status(contract_id, "awaiting_input")

    contract = (await client.get(f"/api/v1/contracts/{contract_id}")).json()
    assert contract["status"] == "awaiting_input"


async def test_uploading_a_reference_makes_its_text_available_to_the_agent_workspace(
    client: AsyncClient, cleanup: list[uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    wire_pipeline(monkeypatch, confidences=(0.3,))  # suspends before drafting; we only check upload

    response = await client.post(
        "/api/v1/contracts",
        data={"request": "Draft an NDA based on the attached commercial terms."},
        files={"files": ("commercial-terms.txt", b"Payment is due within 30 days.", "text/plain")},
    )
    assert response.status_code == 202
    contract_id = uuid.UUID(response.json()["contract_id"])
    cleanup.append(contract_id)

    files = (await client.get(f"/api/v1/contracts/{contract_id}/workspace")).json()
    reference = next(file for file in files if file["path"].startswith("references/"))
    contents = (
        await client.get(f"/api/v1/contracts/{contract_id}/workspace/{reference['path']}")
    ).json()["content"]
    assert contents == "Payment is due within 30 days."


async def test_the_event_stream_replays_the_whole_run(
    client: AsyncClient, cleanup: list[uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    wire_pipeline(monkeypatch, confidences=(0.3,))  # low confidence → Phase A asks and suspends
    body = (await client.post("/api/v1/contracts", json={"request": "Draft an NDA"})).json()
    contract_id = uuid.UUID(body["contract_id"])
    cleanup.append(contract_id)

    await _wait_for_status(contract_id, "awaiting_input")

    events = await _read_sse(client, body["run_id"])
    types = [e["event"] for e in events]

    assert types[0] == "stage", "the run opens with a stage event"
    assert "stage" in types, "phase progress reaches the client"
    assert types[-1] == "input_required", "terminal event ends the stream"

    questions = json.loads(events[-1]["data"])["questions"]
    assert questions, "the suspending event carries the questions to answer"
    assert questions[0]["name"].startswith("clarification_")


async def test_a_client_can_resume_the_stream_from_a_seq(
    client: AsyncClient, cleanup: list[uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    wire_pipeline(monkeypatch, confidences=(0.3,))
    body = (await client.post("/api/v1/contracts", json={"request": "Draft an NDA"})).json()
    cleanup.append(uuid.UUID(body["contract_id"]))
    await _wait_for_status(uuid.UUID(body["contract_id"]), "awaiting_input")

    everything = await _read_sse(client, body["run_id"])
    resumed = await _read_sse(client, body["run_id"], seq=2)

    assert [e["id"] for e in everything][:2] == ["1", "2"]
    assert [e["id"] for e in resumed] == [e["id"] for e in everything[2:]]


async def _read_sse(client: AsyncClient, run_id: str, seq: int = 0) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    url = f"/api/v1/runs/{run_id}/events"
    async with client.stream("GET", url, params={"seq": seq}, timeout=10.0) as response:
        assert response.status_code == 200
        current: dict[str, str] = {}
        async for line in response.aiter_lines():
            if not line:
                if current:
                    events.append(current)
                    current = {}
                continue
            key, _, value = line.partition(": ")
            current[key] = value
    return events


# ---------------------------------------------------------------------------- answers


async def test_answering_resumes_the_run(
    client: AsyncClient, cleanup: list[uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Low confidence first (the run asks), then high on resume (it drafts and finalizes).
    wire_pipeline(monkeypatch, confidences=(0.3, 0.95))
    body = (await client.post("/api/v1/contracts", json={"request": "Draft an NDA"})).json()
    contract_id = uuid.UUID(body["contract_id"])
    cleanup.append(contract_id)
    await _wait_for_status(contract_id, "awaiting_input")

    response = await client.post(
        f"/api/v1/contracts/{contract_id}/answers",
        json={"answers": {"clarification_1": "An NDA governed by Indian law."}},
    )
    assert response.status_code == 202

    await _wait_for_status(contract_id, "ready")

    events = await _read_sse(client, response.json()["run_id"])
    assert events[-1]["event"] == "complete"
    assert "finalized" in json.loads(events[-1]["data"])["message"]


async def test_answering_a_contract_that_is_not_waiting_is_409(
    client: AsyncClient, cleanup: list[uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    wire_pipeline(monkeypatch, confidences=(0.95,))  # high confidence → drafts straight through
    body = (await client.post("/api/v1/contracts", json={"request": "Draft an NDA"})).json()
    contract_id = uuid.UUID(body["contract_id"])
    cleanup.append(contract_id)
    await _wait_for_status(contract_id, "ready")

    response = await client.post(
        f"/api/v1/contracts/{contract_id}/answers", json={"answers": {"x": "y"}}
    )
    assert response.status_code == 409
    assert "not awaiting input" in response.json()["detail"]


async def test_an_empty_answers_body_is_rejected(client: AsyncClient) -> None:
    response = await client.post(f"/api/v1/contracts/{uuid.uuid4()}/answers", json={"answers": {}})
    assert response.status_code == 422


# ----------------------------------------------------------------------------- export


async def test_export_refuses_a_contract_with_no_finalized_version(
    client: AsyncClient, cleanup: list[uuid.UUID]
) -> None:
    """An HTTP client is not a way around the choke point."""
    factory = get_session_factory()
    async with factory() as s:
        contract = Contract(contract_type="nda", request="Draft an NDA", status="drafting")
        s.add(contract)
        await s.commit()
        cleanup.append(contract.id)
        s.add(
            ContractVersion(
                contract_id=contract.id, attempt=1, path="draft_v1.md", markdown="# NDA", score=99
            )
        )
        await s.commit()

    response = await client.post(f"/api/v1/contracts/{contract.id}/export")
    assert response.status_code == 409
    assert "has not passed the gates" in response.json()["detail"]


async def test_a_finalized_contract_exports_and_downloads(
    client: AsyncClient, cleanup: list[uuid.UUID]
) -> None:
    from backend.core.run_context import RunContext
    from backend.tools.finalize_tool import finalize_contract

    factory = get_session_factory()
    async with factory() as s:
        contract = Contract(contract_type="nda", request="Draft an NDA", status="drafting")
        s.add(contract)
        await s.commit()
        cleanup.append(contract.id)

    ctx = RunContext(contract_id=contract.id, session_factory=factory, contract_type="nda")
    await render_clauses_for(ctx)
    markdown = await faithful_markdown(ctx)
    async with factory() as s:
        s.add(
            ContractVersion(
                contract_id=contract.id, attempt=1, path="draft_v1.md", markdown=markdown, score=95
            )
        )
        await s.commit()
    await finalize_contract.on_invoke_tool(tool_ctx(ctx, "finalize_contract"), "{}")

    created = await client.post(f"/api/v1/contracts/{contract.id}/export")
    assert created.status_code == 200
    export = created.json()
    assert export["format"] == "docx" and export["size_bytes"] > 0

    again = await client.post(f"/api/v1/contracts/{contract.id}/export")
    assert again.json()["id"] == export["id"], "byte-stable, so the same document"

    download = await client.get(f"/api/v1/exports/{export['id']}")
    assert download.status_code == 200
    assert download.headers["content-type"] == DOCX_MEDIA
    assert download.headers["x-content-sha256"] == export["sha256"]
    assert download.content[:2] == b"PK"


async def test_unknown_export_is_404(client: AsyncClient) -> None:
    assert (await client.get(f"/api/v1/exports/{uuid.uuid4()}")).status_code == 404


# ------------------------------------------------------------------- inspection routes


async def test_the_workspace_and_versions_are_inspectable(
    client: AsyncClient, cleanup: list[uuid.UUID]
) -> None:
    from backend.core.run_context import RunContext

    factory = get_session_factory()
    async with factory() as s:
        contract = Contract(contract_type="nda", request="Draft an NDA")
        s.add(contract)
        await s.commit()
        cleanup.append(contract.id)

    ctx = RunContext(contract_id=contract.id, session_factory=factory, contract_type="nda")
    await render_clauses_for(ctx)
    async with factory() as s:
        s.add(
            ContractVersion(
                contract_id=contract.id, attempt=1, path="draft_v1.md", markdown="# NDA", score=88
            )
        )
        await s.commit()

    files = (await client.get(f"/api/v1/contracts/{contract.id}/workspace")).json()
    assert all(f["read_only"] for f in files)
    assert any(f["path"] == "clauses/nda.duration.md" for f in files)

    body = (
        await client.get(f"/api/v1/contracts/{contract.id}/workspace/clauses/nda.duration.md")
    ).json()
    assert "clause_id: nda.duration" in body["content"]

    missing = await client.get(f"/api/v1/contracts/{contract.id}/workspace/nope.md")
    assert missing.status_code == 404

    versions = (await client.get(f"/api/v1/contracts/{contract.id}/versions")).json()
    assert versions[0]["attempt"] == 1 and versions[0]["score"] == 88
    assert versions[0]["finalized_at"] is None


async def test_a_run_row_is_created_and_finished(
    client: AsyncClient, cleanup: list[uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    wire_pipeline(monkeypatch, confidences=(0.95,))
    body = (await client.post("/api/v1/contracts", json={"request": "Draft an NDA"})).json()
    contract_id = uuid.UUID(body["contract_id"])
    cleanup.append(contract_id)
    run = await _wait_for_run_status(uuid.UUID(body["run_id"]), "complete")

    assert run.status == "complete"
    assert run.finished_at is not None
