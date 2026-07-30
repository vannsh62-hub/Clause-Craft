"""Export endpoints.

`POST /contracts/{id}/export` calls the same code the agent's `export_docx` tool calls, and is
subject to the same gate: only a version that `finalize_contract` finalized can become a
document. An HTTP client is not a way around the choke point.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_session
from backend.api.schemas import ExportOut
from backend.core.run_context import RunContext
from backend.invariants.export import docx_sha256, markdown_to_docx
from backend.storage import get_storage
from backend.tools.validation_tool import load_rendered_clauses
from backend.workspace.models import Contract, ContractVersion, Export

router = APIRouter(tags=["exports"])

DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_TITLE = {"nda": "Non-Disclosure Agreement", "service": "Services Agreement"}


@router.post("/contracts/{contract_id}/export", response_model=ExportOut)
async def create_export(
    contract_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> Export:
    contract = await session.get(Contract, contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="no such contract")

    version = (
        await session.execute(
            select(ContractVersion).where(
                ContractVersion.contract_id == contract_id,
                ContractVersion.finalized_at.is_not(None),
            )
        )
    ).scalar_one_or_none()

    if version is None:
        raise HTTPException(
            status_code=409,
            detail="no finalized version; the contract has not passed the gates",
        )

    # Prefer the drafting engine's rendered DOCX. In template mode these are the edited
    # source bytes — the whole point of Mode 2 — and regenerating from markdown would discard
    # the original formatting. Fall back to regeneration only for rows with no stored DOCX
    # (the old orchestrator path and legacy versions).
    storage = get_storage()
    if version.docx_storage_key and storage.exists(version.docx_storage_key):
        payload = storage.get(version.docx_storage_key)
    else:
        from backend.api.deps import get_session_factory

        context = RunContext(
            contract_id=contract_id,
            session_factory=get_session_factory(),
            contract_type=contract.contract_type,
            jurisdiction=contract.jurisdiction,
        )
        clauses = await load_rendered_clauses(context)
        payload = markdown_to_docx(
            version.markdown,
            title=_TITLE.get(contract.contract_type or "", "Contract"),
            clauses=clauses,
        )
    digest = docx_sha256(payload)

    existing = (
        await session.execute(
            select(Export).where(
                Export.contract_version_id == version.id,
                Export.format == "docx",
                Export.sha256 == digest,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    key = f"{uuid.uuid4()}.docx"
    get_storage().put(key, payload)

    export = Export(
        contract_version_id=version.id,
        format="docx",
        storage_key=key,
        sha256=digest,
        size_bytes=len(payload),
    )
    session.add(export)
    await session.commit()
    await session.refresh(export)
    return export


@router.get("/exports/{export_id}")
async def download_export(
    export_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> Response:
    export = await session.get(Export, export_id)
    if export is None:
        raise HTTPException(status_code=404, detail="no such export")

    payload = get_storage().get(export.storage_key)
    return Response(
        content=payload,
        media_type=DOCX_MEDIA,
        headers={
            "Content-Disposition": f'attachment; filename="contract-{export_id}.docx"',
            "X-Content-SHA256": export.sha256,
        },
    )
