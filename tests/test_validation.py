"""The two validators, one case per gate, and the finalization they gate.

Legal validation asks "is the contract correct?"; document validation asks "is it
well-formed?". A blocker in either refuses finalization — the invariant from spec 01, that
there is no path to a document that goes around the gates.

Each gate gets a case that trips exactly it, so a regression names the gate that broke.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.artifacts import Artifact, ArtifactStore
from backend.core.config import settings
from backend.core.run_context import RunContext
from backend.invariants.structure import (
    check_cross_references,
    check_definitions,
    check_duplicate_sections,
    check_numbering,
    check_placeholders,
)
from backend.phase_b.finalize import finalize
from backend.phase_b.validation_document import validate_document
from backend.phase_b.validation_legal import validate_legal
from backend.schemas.cko import ContractKnowledgeObject
from backend.schemas.intent import IntentObject, ResolutionPlan
from backend.schemas.playbook import PlaybookRequirement
from backend.schemas.validation import GateReport
from backend.workspace.models import Contract
from backend.workspace.store import REFERENCE_PREFIX, WorkspaceStore

CLEAN_DRAFT = (
    "## Confidentiality\nThe receiving party shall keep information secret.\n\n"
    "## Governing Law\nThis agreement is governed by the laws of India.\n"
)


def _cko(contract_id: uuid.UUID, **overrides: object) -> ContractKnowledgeObject:
    base: dict[str, object] = {
        "contract_id": contract_id,
        "resolution": ResolutionPlan(providers=("llm",)),
        "intent": IntentObject(contract_type="nda", confidence=0.9, jurisdiction="IN"),
    }
    base.update(overrides)
    return ContractKnowledgeObject(**base)  # type: ignore[arg-type]


@pytest_asyncio.fixture
async def ctx() -> AsyncIterator[RunContext]:
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    cid = uuid.uuid4()
    async with factory() as s:
        s.add(Contract(id=cid, contract_type="nda", request="Draft an NDA"))
        await s.commit()
    try:
        yield RunContext(contract_id=cid, session_factory=factory, contract_type="nda")
    finally:
        async with factory() as s:
            await s.execute(delete(Contract).where(Contract.id == cid))
            await s.commit()
        await engine.dispose()


# ----------------------------------------------- the structural gates (pure, one each)


def test_placeholder_gate_flags_but_does_not_block() -> None:
    """A placeholder is a legitimate draft output — a marked gap the user fills, not an error.

    Blocking on it made "draft a lease" a dead end. It is now a `major` flag: the draft
    finalizes, and the slot is named so the user knows what to complete.
    """
    findings = check_placeholders("## Fee\nThe fee is {{ amount }} and [MONTHLY RENT].")

    assert findings and findings[0].severity == "major"
    assert "{{ amount }}" in findings[0].message
    assert "[MONTHLY RENT]" in findings[0].message


def test_a_bare_all_caps_bracket_is_recognised_as_a_placeholder() -> None:
    """The default a model reaches for — and the one the old detector missed, since it has
    no 'insert' in it."""
    findings = check_placeholders("## Property\nThe premises at [PROPERTY ADDRESS].")
    assert findings and "[PROPERTY ADDRESS]" in findings[0].message


def test_citation_and_sub_clause_brackets_are_not_placeholders() -> None:
    """`[1]` and `[a]` are references, not gaps; flagging them would cry wolf."""
    assert check_placeholders("As held in [1], the sub-clause [a] applies.") == []


def test_placeholder_gate_passes_a_clean_draft() -> None:
    assert check_placeholders(CLEAN_DRAFT) == []


def test_numbering_gate_flags_a_gap() -> None:
    findings = check_numbering("## 1. First\n## 3. Third")
    assert findings and "3" in findings[0].message


def test_numbering_gate_flags_a_duplicate() -> None:
    findings = check_numbering("## 1. First\n## 1. Also first")
    assert any("twice" in f.message for f in findings)


def test_numbering_gate_passes_a_consecutive_run() -> None:
    assert check_numbering("## 1. First\n## 2. Second\n## 3. Third") == []


def test_cross_reference_gate_flags_a_dangling_pointer() -> None:
    draft = "## One\nSee §5 for details.\n## Two\nMore.\n"  # only 2 sections, §5 dangles
    findings = check_cross_references(draft)
    assert findings and "5" in findings[0].message


def test_cross_reference_gate_passes_a_valid_pointer() -> None:
    draft = "## One\nSee §2.\n## Two\nHere.\n"
    assert check_cross_references(draft) == []


def test_definitions_gate_flags_a_defined_but_unused_term() -> None:
    findings = check_definitions("## Body\nNothing relevant here.", ["Affiliate"])
    assert findings and "Affiliate" in findings[0].message


def test_definitions_gate_passes_a_used_term() -> None:
    assert check_definitions("## Body\nThe Affiliate agrees.", ["Affiliate"]) == []


def test_conflict_gate_blocks_two_governing_law_sections() -> None:
    findings = check_duplicate_sections(["Governing Law", "Confidentiality", "Governing Law"])
    assert findings and findings[0].severity == "blocker"


def test_conflict_gate_passes_a_single_governing_law_section() -> None:
    assert check_duplicate_sections(["Governing Law", "Confidentiality"]) == []


# ----------------------------------------------------------------- the legal validator


async def test_legal_completeness_blocks_a_missing_required_section(ctx: RunContext) -> None:
    cko = _cko(
        ctx.contract_id,
        playbook_rules=(
            PlaybookRequirement(
                rule_id="dpdp", kind="require_section", target="data_protection", reason="DPDP"
            ),
        ),
    )
    draft = "## Confidentiality\nSecret.\n"  # no data-protection section

    report = await validate_legal(draft, cko, ctx)

    assert not report.passed
    assert any("data_protection" in f.message for f in report.blockers)


async def test_legal_reference_leakage_blocks_a_copied_passage(ctx: RunContext) -> None:
    reference = (
        "The Receiving Party shall protect the ZORBLAX-QUUX-7719 materials in strict "
        "confidence and shall not disclose them to any third party without consent."
    )
    async with ctx.session_factory() as s:
        await WorkspaceStore(s).write(ctx.contract_id, f"{REFERENCE_PREFIX}01-ref.txt", reference)
        await s.commit()

    leaked = f"## Confidentiality\n{reference}\n## Governing Law\nIndia.\n"
    report = await validate_legal(leaked, _cko(ctx.contract_id), ctx)

    assert not report.passed
    blocker = next(f for f in report.blockers if "copied word-for-word" in f.message)
    assert "ref" in blocker.message, "the finding names the document it came from"
    assert "Use as a template" in (blocker.fix_hint or ""), "and what to do instead"


async def test_legal_validator_passes_a_clean_draft(ctx: RunContext) -> None:
    report = await validate_legal(CLEAN_DRAFT, _cko(ctx.contract_id), ctx)
    assert report.passed
    assert report.kind == "legal"


# -------------------------------------------------------------- the document validator


async def test_document_validator_flags_a_placeholder_without_blocking(ctx: RunContext) -> None:
    report = await validate_document("## Fee\nThe fee is {{ amount }}.", _cko(ctx.contract_id), ctx)

    assert report.passed, "a placeholder no longer blocks the document"
    assert any(f.dimension == "placeholders" for f in report.findings), "but it is reported"


async def test_document_validator_passes_a_clean_draft(ctx: RunContext) -> None:
    report = await validate_document(CLEAN_DRAFT, _cko(ctx.contract_id), ctx)
    assert report.passed


# ------------------------------------------------------------------- finalize refuses


async def test_finalize_refuses_on_a_legal_blocker(ctx: RunContext) -> None:
    cko = _cko(
        ctx.contract_id,
        playbook_rules=(
            PlaybookRequirement(rule_id="dpdp", kind="require_section", target="data_protection"),
        ),
    )
    decision = await finalize("## Confidentiality\nSecret.\n", cko, ctx)

    assert decision.finalized is False
    assert decision.blockers


async def test_finalize_flags_but_accepts_a_draft_with_placeholders(ctx: RunContext) -> None:
    """The Mike behaviour: a draft with fill-in slots is finalized-and-flagged, not refused."""
    decision = await finalize("## Fee\nThe fee is {{ amount }}.", _cko(ctx.contract_id), ctx)

    assert decision.finalized is True, "placeholders no longer refuse the document"
    assert decision.needs_review is True, "but it is flagged for the user to complete"
    assert any(f.dimension == "placeholders" for f in decision.flags)


async def test_finalize_still_refuses_on_a_real_blocker(ctx: RunContext) -> None:
    """A genuinely broken contract — here, a required playbook section left out — still stops.
    The placeholder change loosened one gate, not the choke point itself."""
    from backend.schemas.playbook import PlaybookRequirement

    cko = _cko(
        ctx.contract_id,
        playbook_rules=(
            PlaybookRequirement(
                rule_id="dpdp",
                kind="require_section",
                target="Data Protection",
                reason="DPDP Act",
                blocking=True,
            ),
        ),
    )
    decision = await finalize("## Confidentiality\nStandard terms apply.", cko, ctx)

    assert decision.finalized is False
    assert any(f.dimension == "completeness" for f in decision.blockers)


async def test_finalize_accepts_a_clean_draft(ctx: RunContext) -> None:
    decision = await finalize(CLEAN_DRAFT, _cko(ctx.contract_id), ctx)
    assert decision.finalized is True
    assert decision.blockers == ()


async def test_finalize_persists_both_reports(ctx: RunContext) -> None:
    """Validation is part of the audit trail, not a transient check."""
    await finalize(CLEAN_DRAFT, _cko(ctx.contract_id), ctx)

    artifacts = ArtifactStore(ctx.session_factory, ctx.contract_id)
    legal = await artifacts.load(Artifact.VALIDATION_LEGAL)
    document = await artifacts.load(Artifact.VALIDATION_DOCUMENT)
    assert isinstance(legal, GateReport) and legal.kind == "legal"
    assert isinstance(document, GateReport) and document.kind == "document"


async def test_a_major_finding_does_not_refuse_finalization(ctx: RunContext) -> None:
    """The system never silently withholds a document. A numbering gap is `major` — recorded
    and surfaced, but it does not void the contract, so it does not block."""
    draft = "## 1. First\n## 3. Third\n## Governing Law\nIndia.\n"  # numbering gap = major
    decision = await finalize(draft, _cko(ctx.contract_id), ctx)

    assert decision.finalized is True, "a major finding is surfaced, not blocking"
    assert any(f.severity == "major" for f in decision.document.findings)
