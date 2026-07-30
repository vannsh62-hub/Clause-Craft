"""Drafting — the transformation plan becomes a document.

The precondition established at M8 still holds and is still first: no drafting without a
transformation plan, checked before the runtime is touched. What M9 adds is the part after
the gate — turning the plan and the drafted text into an actual DOCX.

## Two modes, one gate

- **Mode 2 (a template was uploaded)** — the document is *edited in place*. The KEEP
  sections are never touched, so their formatting survives by construction; only MODIFY,
  REMOVE and ADD change anything. `apply_transformation` does this from the source bytes;
  regenerating the document and copying content across would be a defect, because it loses
  exactly the formatting the upload was meant to preserve.
- **Mode 1 (no template)** — there is nothing to preserve, so the document is generated
  from the drafted sections in plan order.

The mode is read off the CKO, not passed in: `source_storage_key` is present iff a document
was uploaded. Phase B receives the CKO and decides from it.

## The drafting agent produces text, not a document

It returns `DraftedContent` — text for each MODIFY and ADD section, keyed by the exact ref
the DOCX editor will place it by. Assembling and formatting is deterministic code, because
it has a right answer; the model's job is the words.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from backend.artifacts import Artifact, ArtifactStore
from backend.core.config import settings
from backend.core.logging import get_logger
from backend.core.prompts import load_prompt
from backend.core.run_context import RunContext
from backend.invariants.docx_build import document_to_text, render_contract
from backend.invariants.docx_edit import apply_transformation
from backend.invariants.export import docx_sha256
from backend.phase_b.document import build_contract_document
from backend.runtime.adapters.openai_agents import runtime
from backend.runtime.port import AgentRuntime
from backend.runtime.spec import AgentSpec
from backend.schemas.cko import ContractKnowledgeObject
from backend.schemas.plan import DraftedContent, SectionDecision, TransformationPlan
from backend.storage import get_storage
from backend.workspace.ledger import count_attempts
from backend.workspace.models import ContractVersion

__all__ = ["RUNTIME", "DraftResult", "build_drafting_spec", "draft"]

log = get_logger(__name__)

#: Replaced in tests. One seam for every spec-driven agent.
RUNTIME: AgentRuntime = runtime

DRAFT_PATH = "draft_v{n}.docx"


@dataclass(frozen=True)
class DraftResult:
    """A completed drafting attempt.

    `storage_key` points at the generated DOCX; `markdown` is the text of the draft, kept
    for the validation gates that reason over text rather than over a binary. `warnings`
    surfaces anything the DOCX editor could not do cleanly — an ADD it had to place without
    a styled donor — so the document validator sees it rather than a log file.
    """

    contract_id: str
    attempt: int
    storage_key: str
    sha256: str
    markdown: str
    sections_touched: int
    warnings: tuple[str, ...] = ()
    mode: str = "template"


def build_drafting_spec() -> AgentSpec[DraftedContent]:
    """The drafting agent, as data.

    Structured output: text per section, keyed by ref. No planning tools — the plan is
    authority, and a drafter that could reclassify would be second-guessing a decision made
    on evidence it may not have.
    """
    return AgentSpec(
        name="drafting_agent",
        prompt=load_prompt("drafting_v2"),
        model=settings.drafting_model,
        output_model=DraftedContent,
        max_turns=4,
        temperature=0.2,
    )


async def draft(cko: ContractKnowledgeObject, ctx: RunContext) -> DraftResult:
    """Draft the contract from its transformation plan.

    The transformation plan is required, and checked first — before the runtime is touched.
    A missing plan is a free failure (see M8).
    """
    # THE GATE. Before the runtime, before the agent, before any token.
    artifacts = ArtifactStore(ctx.session_factory, ctx.contract_id)
    plan = await artifacts.require(Artifact.TRANSFORMATION_PLAN)
    assert isinstance(plan, TransformationPlan)

    result = await RUNTIME.run(build_drafting_spec(), ctx, _instruction(cko, plan))
    assert result.output is not None
    drafted = result.output
    new_text = {section.ref: section.text for section in drafted.sections}

    if cko.source_storage_key is not None:
        docx_bytes, markdown, warnings = _draft_mode_2(cko.source_storage_key, plan, new_text)
        mode = "template"
    else:
        docx_bytes, markdown, warnings = _draft_mode_1(cko, plan, new_text, drafted.recitals)
        mode = "ai_drafting"

    attempt, storage_key = await _record(ctx, docx_bytes, markdown)
    return DraftResult(
        contract_id=str(cko.contract_id),
        attempt=attempt,
        storage_key=storage_key,
        sha256=docx_sha256(docx_bytes),
        markdown=markdown,
        sections_touched=len(plan.all_decisions),
        warnings=tuple(warnings),
        mode=mode,
    )


def _draft_mode_2(
    source_key: str,
    plan: TransformationPlan,
    new_text: dict[str, str],
) -> tuple[bytes, str, list[str]]:
    """Edit the uploaded document in place.

    The source bytes come from storage; the KEEP sections in them are never touched, which
    is the whole of the fidelity guarantee. `apply_transformation` raises if `new_text` is
    missing a MODIFY or ADD — an empty clause in an executed contract is worse than a
    failed run.
    """
    source = get_storage().get(source_key)
    docx_bytes, report = apply_transformation(source, plan, new_text)
    markdown = _markdown_of(plan, new_text)
    return docx_bytes, markdown, report.warnings


def _draft_mode_1(
    cko: ContractKnowledgeObject,
    plan: TransformationPlan,
    new_text: dict[str, str],
    recitals: tuple[str, ...] = (),
) -> tuple[bytes, str, list[str]]:
    """Generate a document from the plan. Nothing to preserve, so nothing to edit.

    Builds a `ContractDocument` — title, party block, recitals, numbered clauses, execution
    page — and renders both the DOCX and the text form from that one object. Deriving both
    from the same tree is what keeps the document the gates validate identical to the
    document the user downloads.
    """
    document = build_contract_document(cko, plan, new_text, recitals)
    return render_contract(document), document_to_text(document), []


def _markdown_of(plan: TransformationPlan, new_text: dict[str, str]) -> str:
    """The draft as text, for the validation gates that reason over text.

    Sections in plan order: KEEP and MODIFY and ADD, skipping REMOVE. A KEEP with no
    supplied text falls back to its name, since its real text lives in the source document
    and is not rewritten.
    """
    lines: list[str] = []
    for decision in (*plan.keep, *plan.modify, *plan.add):
        key = _key(decision)
        heading = decision.name
        body = new_text.get(key, "")
        lines.append(f"## {heading}")
        if body:
            lines.append(body)
        lines.append("")
    return "\n".join(lines).strip()


def _key(decision: SectionDecision) -> str:
    ref = decision.source_ref
    return (ref.block_id if ref and ref.block_id else None) or decision.name


def _instruction(cko: ContractKnowledgeObject, plan: TransformationPlan) -> str:
    """The drafting brief, naming the exact ref for every slot the agent must fill.

    A MODIFY is keyed by its source block id, an ADD by its section name. If the agent
    returns a different ref, the DOCX editor cannot place the text and the section comes out
    empty — so the refs are not left to the agent to infer, they are handed to it.
    """
    slots = [
        f"- ref `{_key(d)}` — {d.decision.upper()} `{d.name}`: {d.reason}"
        for d in (*plan.modify, *plan.add)
    ]
    slot_block = (
        "\n".join(slots) or "- (no sections need new text; every decision is KEEP or REMOVE)"
    )

    return (
        "Write the text for each slot below. Return one DraftedSection per slot, using the "
        "exact `ref` shown — a different ref means your text is dropped and the section is "
        "left empty.\n\n"
        "## Slots to fill\n"
        f"{slot_block}\n\n"
        "## Transformation plan\n"
        f"{plan.model_dump_json(indent=2)}\n\n"
        "## Contract knowledge\n"
        f"{cko.model_dump_json(indent=2)}"
    )


async def _record(ctx: RunContext, docx_bytes: bytes, markdown: str) -> tuple[int, str]:
    """Persist the DOCX to storage and append a `ContractVersion` row.

    The attempt number is derived server-side from the ledger, never supplied by the model
    — the same rule as the original drafting agent: a model that cannot name the file it
    writes cannot overwrite an earlier attempt while claiming to be a later one.
    """
    storage_key = f"{uuid.uuid4()}.docx"
    get_storage().put(storage_key, docx_bytes)

    async with ctx.session_factory() as session:
        attempt = await count_attempts(session, ctx.contract_id) + 1
        session.add(
            ContractVersion(
                contract_id=ctx.contract_id,
                attempt=attempt,
                path=DRAFT_PATH.format(n=attempt),
                markdown=markdown,
                # The faithful DOCX lives in blob storage; keep the pointer so the export
                # serves these exact bytes rather than regenerating from `markdown`.
                docx_storage_key=storage_key,
            )
        )
        await session.commit()

    log.info("draft attempt=%d key=%s bytes=%d", attempt, storage_key, len(docx_bytes))
    return attempt, storage_key
