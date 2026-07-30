"""The template path, end to end at the edges: intake stores a template, and the export
serves the drafting engine's faithful bytes instead of regenerating from markdown.

These are the two wirings that make "use this document as a template" real: without the first
an upload is only reference text; without the second a perfect in-place edit is thrown away at
download. Each is tested at its boundary so a regression in either is caught on its own.
"""

from __future__ import annotations

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
from backend.phase_a.intent import unmet_conditions
from backend.schemas.intent import IntentObject
from backend.storage import get_storage
from backend.workspace.models import Contract, ContractVersion
from tests.pipeline_fakes import wire_pipeline

FIXTURE = Path(__file__).parent / "data" / "sla-sample.docx"


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


# --------------------------------------------------------------------- intent relaxation


def _intent(contract_type: str) -> IntentObject:
    return IntentObject(
        contract_type=contract_type,
        parties=[],
        jurisdiction="IN",
        purpose="x",
        confidence=0.95,
    )


def test_an_unfamiliar_type_is_not_refused_with_or_without_a_template() -> None:
    """Any document may be asked for.

    The allow-list refusal that used to live here is gone: it turned away types the engine
    drafts perfectly well, and it said nothing about the ones drafted from model knowledge.
    Disclosure is the consent gate's job now (`test_consent_gate.py`), which knows whether
    approved clauses exist because it runs after resolution.
    """
    assert unmet_conditions(_intent("reseller"), has_template=False) == ()
    assert unmet_conditions(_intent("reseller"), has_template=True) == ()


# ------------------------------------------------------------------------ template intake


async def test_uploading_as_template_stores_a_template_not_a_reference(
    client: AsyncClient, cleanup: list[uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Low confidence so the run suspends at intent — the template is stored at intake, before
    # the run, so we can assert on it without driving the whole pipeline.
    wire_pipeline(monkeypatch, confidences=(0.3,))

    response = await client.post(
        "/api/v1/contracts",
        data={"request": "Use this document as a template and adapt it.", "as_template": "true"},
        files={
            "files": (
                "sla-sample.docx",
                FIXTURE.read_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert response.status_code == 202
    contract_id = uuid.UUID(response.json()["contract_id"])
    cleanup.append(contract_id)

    files = (await client.get(f"/api/v1/contracts/{contract_id}/workspace")).json()
    paths = {f["path"] for f in files}
    assert POINTER_PATH in paths, "the upload was stored as a template"
    assert not any(p.startswith("references/") for p in paths), "and not as a reference"


async def test_a_non_docx_template_is_rejected(
    client: AsyncClient, cleanup: list[uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    wire_pipeline(monkeypatch, confidences=(0.3,))
    response = await client.post(
        "/api/v1/contracts",
        data={"request": "Use this as a template.", "as_template": "true"},
        files={"files": ("notes.txt", b"just some text", "text/plain")},
    )
    assert response.status_code == 415
    assert "must be a .docx" in response.json()["detail"]


# ------------------------------------------------------------------------------ deal terms


def test_the_intent_carries_the_values_the_user_stated() -> None:
    """The operative numbers must survive into the object Phase B reads.

    They previously had nowhere to live: the intent recorded type, parties and law but not
    the deal's substance, so a request saying "99.9% uptime" reached the drafter with no
    figure in it. The drafter, forbidden to invent one, then either left a placeholder (which
    the document gate blocks) or wrote a vague cross-reference — silently dropping what the
    user asked for.
    """
    from backend.schemas.intent import DealTerm

    intent = IntentObject(
        contract_type="sla",
        parties=[],
        jurisdiction="IN",
        purpose="x",
        confidence=0.95,
        deal_terms=[
            DealTerm(name="uptime", value="99.9% per calendar month"),
            DealTerm(name="response_time", value="4 hours during business hours"),
        ],
    )

    assert {t.name: t.value for t in intent.deal_terms}["uptime"] == "99.9% per calendar month"
    # The drafter is handed the CKO as JSON, so the value must be present in that rendering.
    assert "99.9%" in intent.model_dump_json()


# -------------------------------------------------------------------- contract type record


async def test_the_contract_type_phase_a_determined_reaches_the_contract_row(
    cleanup: list[uuid.UUID],
) -> None:
    """Phase A writes the type into the intent artifact; the row is what everything else
    reads. Without this copy the contracts list, the export title and the UI all see null."""
    from backend.api.pipeline_adapter import _record_contract_type
    from backend.artifacts import Artifact, ArtifactStore
    from backend.core.run_context import RunContext

    factory = get_session_factory()
    async with factory() as s:
        contract = Contract(request="draft an sla", status="planning")
        s.add(contract)
        await s.commit()
        cleanup.append(contract.id)

    ctx = RunContext(contract_id=contract.id, session_factory=factory)
    await ArtifactStore(factory, contract.id).save(Artifact.INTENT, _intent("sla"))

    await _record_contract_type(ctx)

    async with factory() as s:
        refreshed = await s.get(Contract, contract.id)
        assert refreshed is not None
        assert refreshed.contract_type == "sla"


# ------------------------------------------------------------------------- export fidelity


async def test_export_serves_the_stored_docx_bytes_not_a_regeneration(
    client: AsyncClient, cleanup: list[uuid.UUID]
) -> None:
    """A finalized version that carries a rendered DOCX exports *those exact bytes*.

    Proven by storing a document (the fixture) whose bytes markdown regeneration could never
    reproduce, then asserting the download is byte-identical.
    """
    original = FIXTURE.read_bytes()
    key = f"{uuid.uuid4()}.docx"
    get_storage().put(key, original)

    factory = get_session_factory()
    async with factory() as s:
        contract = Contract(contract_type="service", request="x", status="ready")
        s.add(contract)
        await s.commit()
        cleanup.append(contract.id)
        from sqlalchemy import func as sa_func

        s.add(
            ContractVersion(
                contract_id=contract.id,
                attempt=1,
                path="draft_v1.docx",
                markdown="# Something entirely different from the fixture",
                docx_storage_key=key,
                finalized_at=sa_func.now(),
            )
        )
        await s.commit()

    created = await client.post(f"/api/v1/contracts/{contract.id}/export")
    assert created.status_code == 200
    export = created.json()

    download = await client.get(f"/api/v1/exports/{export['id']}")
    assert download.status_code == 200
    assert download.content == original, "the export served the stored bytes, not a regeneration"
