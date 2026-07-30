"""`POST /contracts/{id}/document/save` — persisting the Document Preview/Editing view.

Before this endpoint, edits (manual WYSIWYG changes, applied assistant clause actions)
lived only in the frontend's React state: a reload, or `/export`, silently reverted to
whatever the drafting engine originally produced. These tests cover the write path: the
`final.md` workspace file, the finalized `ContractVersion.markdown` column that `/export`
reads, and the `docx_storage_key` invalidation that keeps a stale template-mode docx from
being served once the markdown has diverged from it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest
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


@pytest_asyncio.fixture
async def contract() -> AsyncIterator[Contract]:
    factory = get_session_factory()
    row = Contract(id=uuid.uuid4(), contract_type="nda", request="an NDA", variables={})
    async with factory() as s:
        s.add(row)
        await s.commit()
    try:
        yield row
    finally:
        async with factory() as s:
            await s.execute(delete(Contract).where(Contract.id == row.id))
            await s.commit()


@pytest_asyncio.fixture
async def finalized_version(contract: Contract) -> AsyncIterator[ContractVersion]:
    factory = get_session_factory()
    row = ContractVersion(
        contract_id=contract.id,
        attempt=1,
        path="draft_v1.md",
        markdown="# NDA\n\nOriginal text.\n",
        docx_storage_key="some-template-mode-key.docx",
        finalized_at=datetime.now(timezone.utc),
    )
    async with factory() as s:
        s.add(row)
        await s.commit()
        await s.refresh(row)
    yield row


async def test_save_updates_the_workspace_file_the_preview_reloads_from(
    client: AsyncClient, contract: Contract, finalized_version: ContractVersion
) -> None:
    resp = await client.post(
        f"/api/v1/contracts/{contract.id}/document/save",
        json={"markdown": "# NDA\n\nEdited text.\n"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"] == "final.md"
    assert body["version_id"] == str(finalized_version.id)

    read = await client.get(f"/api/v1/contracts/{contract.id}/workspace/final.md")
    assert read.status_code == 200
    assert read.json()["content"] == "# NDA\n\nEdited text.\n"


async def test_save_overwrites_final_md_on_a_second_call(
    client: AsyncClient, contract: Contract, finalized_version: ContractVersion
) -> None:
    await client.post(
        f"/api/v1/contracts/{contract.id}/document/save", json={"markdown": "first edit\n"}
    )
    resp = await client.post(
        f"/api/v1/contracts/{contract.id}/document/save", json={"markdown": "second edit\n"}
    )
    assert resp.status_code == 200

    read = await client.get(f"/api/v1/contracts/{contract.id}/workspace/final.md")
    assert read.json()["content"] == "second edit\n"


async def test_save_updates_the_finalized_version_markdown_column(
    client: AsyncClient, contract: Contract, finalized_version: ContractVersion
) -> None:
    """`/export` regenerates from `ContractVersion.markdown`, not the workspace, when there's
    no stored docx — so the column has to move too, not just the workspace file."""
    await client.post(
        f"/api/v1/contracts/{contract.id}/document/save",
        json={"markdown": "# NDA\n\nUpdated for export.\n"},
    )

    factory = get_session_factory()
    async with factory() as s:
        refreshed = await s.get(ContractVersion, finalized_version.id)
        assert refreshed is not None
        assert refreshed.markdown == "# NDA\n\nUpdated for export.\n"


async def test_save_clears_a_stale_docx_storage_key(
    client: AsyncClient, contract: Contract, finalized_version: ContractVersion
) -> None:
    """Otherwise `/export` would keep serving the original template-mode bytes forever,
    even though the markdown the user is looking at has changed."""
    assert finalized_version.docx_storage_key is not None

    await client.post(
        f"/api/v1/contracts/{contract.id}/document/save", json={"markdown": "changed\n"}
    )

    factory = get_session_factory()
    async with factory() as s:
        refreshed = await s.get(ContractVersion, finalized_version.id)
        assert refreshed is not None
        assert refreshed.docx_storage_key is None


async def test_save_without_a_finalized_version_is_rejected(
    client: AsyncClient, contract: Contract
) -> None:
    resp = await client.post(
        f"/api/v1/contracts/{contract.id}/document/save", json={"markdown": "x\n"}
    )
    assert resp.status_code == 409


async def test_save_against_an_unknown_contract_is_404(client: AsyncClient) -> None:
    resp = await client.post(
        f"/api/v1/contracts/{uuid.uuid4()}/document/save", json={"markdown": "x\n"}
    )
    assert resp.status_code == 404


async def test_save_rejects_empty_markdown(
    client: AsyncClient, contract: Contract, finalized_version: ContractVersion
) -> None:
    resp = await client.post(
        f"/api/v1/contracts/{contract.id}/document/save", json={"markdown": ""}
    )
    assert resp.status_code == 422


async def test_save_rejects_oversized_markdown(
    client: AsyncClient, contract: Contract, finalized_version: ContractVersion
) -> None:
    resp = await client.post(
        f"/api/v1/contracts/{contract.id}/document/save",
        json={"markdown": "x" * 200_001},
    )
    assert resp.status_code == 422


async def test_save_does_not_touch_other_contracts_workspaces(
    client: AsyncClient, contract: Contract, finalized_version: ContractVersion
) -> None:
    """Scoping regression guard: `WorkspaceStore` is keyed by `contract_id`, and this test
    exists so a future refactor that accidentally drops the scoping fails loudly."""
    factory = get_session_factory()
    other = Contract(id=uuid.uuid4(), contract_type="nda", request="another NDA", variables={})
    async with factory() as s:
        s.add(other)
        await s.commit()
    try:
        await client.post(
            f"/api/v1/contracts/{contract.id}/document/save", json={"markdown": "mine\n"}
        )
        other_workspace = await client.get(f"/api/v1/contracts/{other.id}/workspace")
        assert other_workspace.json() == []
    finally:
        async with factory() as s:
            await s.execute(delete(Contract).where(Contract.id == other.id))
            await s.commit()
