"""`export_docx` — the last hole, closed.

It accepts **only** a `contract_version_id` that `finalize_contract` finalized. There is no
overload taking Markdown, no path taking a workspace file, and no argument that lets the agent
name the content. A document can therefore only exist for a draft that passed the gates.

If that were not true, everything upstream would be theatre: an agent that could hand raw
Markdown to a DOCX writer could ship a contract missing its liability clause, whatever
`validate_draft` thought about it.
"""

from __future__ import annotations

import uuid

from agents import RunContextWrapper, function_tool
from sqlalchemy import select

from backend.core.logging import get_logger
from backend.core.run_context import RunContext
from backend.invariants.export import docx_sha256, markdown_to_docx
from backend.schemas.errors import ContractToolError, format_tool_error
from backend.storage import get_storage
from backend.tools.validation_tool import load_rendered_clauses
from backend.workspace.models import ContractVersion, Export

__all__ = ["export_docx"]

log = get_logger(__name__)

_TITLE = {"nda": "Non-Disclosure Agreement", "service": "Services Agreement"}


def _parse_version_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise ContractToolError(
            f"{raw!r} is not a contract version id. Use the id that finalize_contract returned."
        ) from exc


@function_tool(failure_error_function=format_tool_error)
async def export_docx(wrapper: RunContextWrapper[RunContext], contract_version_id: str) -> str:
    """Produce a .docx from a finalized contract version.

    Only a version that `finalize_contract` finalized can be exported. There is no way to
    export a draft, a workspace file, or text you supply.

    Args:
        contract_version_id: the id `finalize_contract` returned.
    """
    context = wrapper.context
    version_id = _parse_version_id(contract_version_id)

    async with context.session_factory() as session:
        version = (
            await session.execute(
                select(ContractVersion).where(
                    ContractVersion.id == version_id,
                    # Scoping is never optional: a version id from another contract is unknown.
                    ContractVersion.contract_id == context.contract_id,
                )
            )
        ).scalar_one_or_none()

    if version is None:
        raise ContractToolError(
            f"no contract version {version_id} for this contract. "
            "Call finalize_contract and use the id it returns."
        )

    if version.finalized_at is None:
        raise ContractToolError(
            f"attempt {version.attempt} was never finalized, so it cannot be exported. "
            "Call finalize_contract first; it will refuse if the draft does not pass the gates."
        )

    clauses = await load_rendered_clauses(context)
    title = _TITLE.get(context.contract_type or "", "Contract")
    payload = markdown_to_docx(version.markdown, title=title, clauses=clauses)
    digest = docx_sha256(payload)

    async with context.session_factory() as session:
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
            return _summary(existing, version.needs_human_review)

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

    log.info("exported version=%s bytes=%d sha=%s", version.id, len(payload), digest[:12])
    return _summary(export, version.needs_human_review)


def _summary(export: Export, needs_review: bool | None) -> str:
    lines = [
        f"Exported as DOCX. export_id={export.id}, {export.size_bytes} bytes, "
        f"sha256 {export.sha256[:12]}.",
        "The document carries a DRAFT — FOR LEGAL REVIEW header, a disclaimer, and an "
        "appendix listing every clause with its version and checksum.",
    ]
    if needs_review:
        lines.append("It is flagged needs_human_review. Say so when you give it to the user.")
    return "\n".join(lines)
