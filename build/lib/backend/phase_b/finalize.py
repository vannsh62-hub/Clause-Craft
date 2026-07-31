"""Finalization: the one path to a finished document, and it refuses on any blocker.

Both validators run. A blocker from *either* — a missing indemnity or a numbering gap —
refuses finalization. This is the invariant carried from spec 01, unchanged in spirit: the
tools are authoritative over correctness, and there is no path to a document that goes around
them.

`finalize` returns a decision, not a document. On a refusal it returns the findings, so the
orchestrator can fix and retry rather than the run dying. The system never returns nothing
silently — a draft that passed the gates but is imperfect is finalized and flagged, never
withheld without a reason.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.artifacts import Artifact, ArtifactStore
from backend.core.logging import get_logger
from backend.core.run_context import RunContext
from backend.phase_b.validation_document import validate_document
from backend.phase_b.validation_legal import validate_legal
from backend.schemas.cko import ContractKnowledgeObject
from backend.schemas.draft import Finding
from backend.schemas.validation import GateReport

__all__ = ["FinalizeDecision", "finalize"]

log = get_logger(__name__)


@dataclass(frozen=True)
class FinalizeDecision:
    """Whether the draft may become a document, and why not if it may not."""

    finalized: bool
    legal: GateReport
    document: GateReport

    @property
    def blockers(self) -> tuple[Finding, ...]:
        return self.legal.blockers + self.document.blockers

    @property
    def flags(self) -> tuple[Finding, ...]:
        """Non-blocking findings — things a reviewer should see but that do not stop the
        document existing. Chief among them: fill-in placeholders."""
        return tuple(
            f
            for report in (self.legal, self.document)
            for f in report.findings
            if f.severity != "blocker"
        )

    @property
    def needs_review(self) -> bool:
        """Finalized, but with something a human must look at before it is signed."""
        return self.finalized and bool(self.flags)


async def finalize(
    draft: str,
    cko: ContractKnowledgeObject,
    ctx: RunContext,
    *,
    source_docx: bytes | None = None,
    edited_docx: bytes | None = None,
    keep_block_ids: tuple[str, ...] = (),
) -> FinalizeDecision:
    """Validate the draft with both gates and decide whether it may be finalized.

    Persists both reports as `09-*` artifacts — the validation is part of the audit trail,
    not a transient check — and refuses if either carries a blocker.
    """
    legal = await validate_legal(draft, cko, ctx)
    document = await validate_document(
        draft,
        cko,
        ctx,
        source_docx=source_docx,
        edited_docx=edited_docx,
        keep_block_ids=keep_block_ids,
    )

    artifacts = ArtifactStore(ctx.session_factory, ctx.contract_id)
    await artifacts.save(Artifact.VALIDATION_LEGAL, legal)
    await artifacts.save(Artifact.VALIDATION_DOCUMENT, document)

    finalized = legal.passed and document.passed
    log.info(
        "finalize legal_blockers=%d document_blockers=%d finalized=%s",
        len(legal.blockers),
        len(document.blockers),
        finalized,
    )
    return FinalizeDecision(finalized=finalized, legal=legal, document=document)
