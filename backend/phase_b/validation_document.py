"""Document validation: is the contract *well-formed*, whatever it says.

The gates that decide whether the document is mechanically sound — an unfilled placeholder,
a numbering gap, a cross-reference to a section that does not exist, and (in template mode) a
KEEP section whose formatting was disturbed or a table that lost structure.

These are cosmetic in origin and serious in effect: a `{{ fee }}` left in an executed
contract, or a §12 that points at nothing, is exactly the kind of defect that makes a
document look — and be — unfinished. Different in kind from the legal gates, so reported by a
different validator.

Deterministic code from `invariants/`. In template mode it also compares the edited document
against the source to confirm the KEEP sections were not disturbed, which is the Mode 2
fidelity promise checked at the finish line rather than assumed.
"""

from __future__ import annotations

from backend.core.run_context import RunContext
from backend.invariants.structure import (
    check_cross_references,
    check_numbering,
    check_placeholders,
)
from backend.schemas.cko import ContractKnowledgeObject
from backend.schemas.draft import Finding
from backend.schemas.validation import GateReport

__all__ = ["validate_document"]


async def validate_document(
    draft: str,
    cko: ContractKnowledgeObject,
    ctx: RunContext,
    *,
    source_docx: bytes | None = None,
    edited_docx: bytes | None = None,
    keep_block_ids: tuple[str, ...] = (),
) -> GateReport:
    """Run the document gates over `draft` and return a `document` report.

    `source_docx`/`edited_docx`/`keep_block_ids` are supplied in template mode so the
    fidelity gate can confirm the KEEP sections survived the edit. Absent them (Mode 1,
    nothing to preserve) the fidelity gate simply does not run.
    """
    findings: list[Finding] = []

    findings.extend(check_placeholders(draft))
    findings.extend(check_numbering(draft))
    findings.extend(check_cross_references(draft))
    findings.extend(await _planned_sections_present(draft, ctx))

    if source_docx is not None and edited_docx is not None:
        findings.extend(_fidelity(source_docx, edited_docx, keep_block_ids))

    return GateReport(kind="document", findings=tuple(findings))


async def _planned_sections_present(draft: str, ctx: RunContext) -> list[Finding]:
    """Every section the draft plan called for must actually be in the document.

    The gates otherwise measure only the text that *is* there, and are silent about text
    that is missing — so a run that lost most of its sections between planning and drafting
    finalized a two-line contract with no parties, no services and no fees, and reported
    success. A contract missing what it was planned to contain is not well-formed, whatever
    the surviving prose looks like.

    Compared on headings, case-insensitively; the draft is assembled from the plan's names,
    so a missing heading means the section was dropped rather than renamed.
    """
    from backend.artifacts import Artifact, ArtifactStore
    from backend.schemas.plan import DraftPlan

    artifacts = ArtifactStore(ctx.session_factory, ctx.contract_id)
    if not await artifacts.exists(Artifact.DRAFT_PLAN):
        return []
    plan = await artifacts.load(Artifact.DRAFT_PLAN)
    if not isinstance(plan, DraftPlan):
        return []

    body = draft.lower()
    missing = [s.name for s in plan.sections if s.name.lower() not in body]
    if not missing:
        return []

    return [
        Finding(
            dimension="completeness",
            severity="blocker",
            message=(
                f"{len(missing)} planned section(s) are missing from the draft: "
                f"{', '.join(missing)}"
            ),
            fix_hint="every section in the draft plan must be drafted, or removed deliberately",
        )
    ]


def _fidelity(
    source_docx: bytes,
    edited_docx: bytes,
    keep_block_ids: tuple[str, ...],
) -> list[Finding]:
    """A KEEP section whose formatting changed is a fidelity failure.

    The whole promise of Mode 2 is that what the user did not ask to change comes back
    unchanged. `compare` checks the KEEP blocks' fingerprints and the shared style parts;
    any disturbance is a blocker, because a silently reformatted executed contract is worse
    than a failed run.
    """
    from backend.invariants.docx_fidelity import compare

    report = compare(source_docx, edited_docx, expect_unchanged=keep_block_ids)
    if report.unchanged:
        return []

    findings: list[Finding] = []
    if report.changed_blocks or report.removed_blocks:
        findings.append(
            Finding(
                dimension="fidelity",
                severity="blocker",
                message="a KEEP section's formatting was disturbed by the edit",
                fix_hint="edit in place; do not regenerate sections that should be preserved",
            )
        )
    if report.changed_parts:
        findings.append(
            Finding(
                dimension="fidelity",
                severity="blocker",
                message=f"a shared style part changed: {', '.join(report.changed_parts)}",
                fix_hint="do not rewrite styles.xml or numbering.xml when editing in place",
            )
        )
    return findings
