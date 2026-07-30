"""Legal validation: is the contract *correct*, whatever it looks like.

The gates that decide whether the document is legally sound — missing required clauses, a
playbook rule unmet, a defined term left dangling, two governing-law clauses, someone else's
reference text copied in. These are semantic, and they are what a lawyer would catch on a
read-through.

Split from document validation on purpose. A missing indemnity and a numbering gap are
different kinds of problem, caught by different checks, fixed by different people. One report
that mixed them would make a reviewer sort the substantive from the cosmetic before starting.

Every gate here is deterministic code from `invariants/`. The validator is wiring, not
judgement: it runs the checks, collects their findings, and stamps the report `legal`.

Phase B, so it reads the CKO and the draft — plus the reference *documents* from the
workspace, which the leakage gate needs and which never entered the CKO by design. Reading
them here to *check for* leaks is the opposite of copying them into a draft.
"""

from __future__ import annotations

from backend.core.run_context import RunContext
from backend.invariants.leakage import find_leaks
from backend.invariants.playbook_rules import unmet_requirements
from backend.invariants.structure import (
    check_definitions,
    check_duplicate_sections,
    headings_of,
)
from backend.schemas.cko import ContractKnowledgeObject
from backend.schemas.draft import Finding
from backend.schemas.validation import GateReport
from backend.workspace.store import REFERENCE_PREFIX, WorkspaceStore

__all__ = ["validate_legal"]


async def validate_legal(
    draft: str,
    cko: ContractKnowledgeObject,
    ctx: RunContext,
) -> GateReport:
    """Run the legal gates over `draft` and return a `legal` report."""
    findings: list[Finding] = []

    findings.extend(_completeness(draft, cko))
    findings.extend(_definitions(draft, cko))
    findings.extend(_conflicts(draft))
    findings.extend(await _reference_leakage(draft, ctx))

    return GateReport(kind="legal", findings=tuple(findings))


def _completeness(draft: str, cko: ContractKnowledgeObject) -> list[Finding]:
    """Every blocking playbook `require_section` must be satisfied.

    An unmet requirement is a compliance gap the playbook was written to prevent — a
    blocker, because the contract is not the contract the policy required.
    """
    return [
        Finding(
            dimension="completeness",
            severity="blocker",
            message=f"required section '{req.target}' is missing ({req.reason})",
            fix_hint=f"add the {req.target} section; required by playbook rule {req.rule_id}",
        )
        for req in unmet_requirements(draft, cko.playbook_rules)
    ]


def _definitions(draft: str, cko: ContractKnowledgeObject) -> list[Finding]:
    return check_definitions(draft, [d.term for d in cko.definitions])


def _conflicts(draft: str) -> list[Finding]:
    return check_duplicate_sections(headings_of(draft))


async def _reference_leakage(draft: str, ctx: RunContext) -> list[Finding]:
    """No verbatim run from a reference document may appear in the draft.

    The structural guarantee (Phase B never sees reference text) is the primary defence;
    this is the check that it held. Reads the reference documents from the workspace — the
    one place they still live — and compares.
    """
    async with ctx.session_factory() as session:
        store = WorkspaceStore(session)
        files = await store.ls(ctx.contract_id)
        references = [
            (f.path, await store.read(ctx.contract_id, f.path))
            for f in files
            if f.path.startswith(REFERENCE_PREFIX)
        ]

    # One finding per source document, not per passage. A copied clause trips the detector
    # several times over, and seven identical sentences telling the user the same thing is a
    # worse report than one that says how much was copied and shows a sample.
    by_document: dict[str, list[str]] = {}
    for hit in find_leaks(draft, references):
        by_document.setdefault(hit.document, []).append(hit.passage)

    findings: list[Finding] = []
    for document, passages in by_document.items():
        sample = passages[0].strip()
        if len(sample) > 120:
            sample = sample[:117] + "…"
        count = f"{len(passages)} passages were" if len(passages) > 1 else "a passage was"
        findings.append(
            Finding(
                dimension="fidelity",
                severity="blocker",
                message=(
                    f"{count} copied word-for-word from the reference document "
                    f"{_display_name(document)} — for example: “{sample}”"
                ),
                fix_hint=(
                    "An attached document is read for context only; its wording is never "
                    "reproduced. To base a new contract on this document instead, attach it "
                    "again with “Use as a template” ticked — that path adapts the document "
                    "itself and keeps its formatting."
                ),
            )
        )
    return findings


def _display_name(path: str) -> str:
    """`references/01-sla-agreement.txt` reads as `sla-agreement` to a person."""
    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return stem.split("-", 1)[-1] if "-" in stem[:3] else stem
