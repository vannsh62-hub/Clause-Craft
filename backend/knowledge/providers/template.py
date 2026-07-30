"""The uploaded document, as a knowledge source.

Mode 2: the user hands over a contract and asks for it to be converted. The document *is*
the knowledge, and — unlike a reference document — its text is authoritative and must
survive into the output. Keeping those two paths apart is the single most commonly
misimplemented part of the system, so they are kept in separate providers, separate storage
prefixes and separate object types, and nothing reconciles them but the aggregator.

## Where the bytes live

Not in the workspace. `workspace_files.content` is a `Text` column, and a 10 MB DOCX in a
text column would be loaded by every listing that touches the row. The binary goes to
`backend/storage/` — which already exists behind a `Storage` protocol and already serves
exports — and the workspace holds a small JSON pointer at `template/source.json`.

That split is also what makes `available()` cheap: deciding whether this run has a template
is a workspace lookup, not a blob read.

## What it contributes

Structure and formatting, never meaning. The `FormattingManifest` is what lets Phase B edit
the document in place rather than regenerate it; the placeholders are what tell it where
values go. Semantic understanding — what these sections *are* — is a separate agent's job
(M6), because parsing has a right answer and interpretation does not.
"""

from __future__ import annotations

import json
import uuid

from backend.artifacts import Artifact, ArtifactStore
from backend.core.logging import get_logger
from backend.core.run_context import RunContext
from backend.invariants.docx_parse import parse_docx
from backend.knowledge.registry import register_provider
from backend.schemas.cko import Provenance
from backend.schemas.errors import WorkspaceError
from backend.schemas.intent import IntentObject
from backend.schemas.provider import KnowledgeContribution
from backend.schemas.template import TemplateObject
from backend.storage import get_storage
from backend.workspace.store import WorkspaceStore

__all__ = ["POINTER_PATH", "TemplateProvider", "store_template"]

log = get_logger(__name__)

#: Where the pointer to the uploaded binary lives. Deliberately under `template/` rather
#: than `references/`: §7's two paths must never be confusable at a call site.
POINTER_PATH = "template/source.json"


async def store_template(
    data: bytes,
    filename: str,
    ctx: RunContext,
) -> str:
    """Put an uploaded document where the template provider will find it.

    Called at intake, before any agent runs. Returns the storage key.

    The document is parsed here rather than at contribute time so that an unusable upload —
    tracked changes, a corrupt package — is refused while the user is still watching,
    rather than several stages into a run they have already waited for.
    """
    key = f"{uuid.uuid4()}.docx"
    parse_docx(data, filename=filename, storage_key=key)  # refuses early; result discarded

    get_storage().put(key, data)
    pointer = json.dumps({"storage_key": key, "filename": filename}, indent=2, sort_keys=True)

    async with ctx.session_factory() as session:
        await WorkspaceStore(session).write(ctx.contract_id, POINTER_PATH, pointer)
        await session.commit()

    log.info("template stored key=%s bytes=%d", key, len(data))
    return key


class TemplateProvider:
    """Structure and formatting from the uploaded document."""

    name = "template"

    async def available(self, intent: IntentObject, ctx: RunContext) -> bool:
        """Was a document uploaded for this run? A workspace lookup, not a blob read."""
        async with ctx.session_factory() as session:
            return await WorkspaceStore(session).exists(ctx.contract_id, POINTER_PATH)

    async def contribute(self, intent: IntentObject, ctx: RunContext) -> KnowledgeContribution:
        async with ctx.session_factory() as session:
            raw = await WorkspaceStore(session).read(ctx.contract_id, POINTER_PATH)
        pointer = json.loads(raw)
        key, filename = pointer["storage_key"], pointer["filename"]

        storage = get_storage()
        if not storage.exists(key):
            # The pointer outlived the blob. Refuse rather than silently drafting from
            # nothing — Phase B in template mode without formatting regenerates the
            # document, losing exactly what the upload was for.
            raise WorkspaceError(
                f"the uploaded document for this contract is missing from storage ({key}). "
                "Re-upload it before drafting."
            )

        template = parse_docx(storage.get(key), filename=filename, storage_key=key)
        await ArtifactStore(ctx.session_factory, ctx.contract_id).save(Artifact.TEMPLATE, template)

        return _contribution(template)


def _contribution(template: TemplateObject) -> KnowledgeContribution:
    """A parsed template, in the shared vocabulary.

    No `sections` and no `clause_candidates`: those carry meaning, and this provider has
    only read structure. Claiming otherwise here would let a parser's guess about what a
    heading means outrank an agent's reading of it.
    """
    return KnowledgeContribution(
        provider="template",
        provenance=Provenance(
            provider="template",
            locator=f"{template.filename}@{template.sha256[:12]}",
        ),
        formatting=template.formatting,
        source_storage_key=template.storage_key,
    )


register_provider(TemplateProvider())
