"""The playbook provider, and the block an unmet requirement produces.

The rule *engine* is tested in `test_playbook_rules.py`. This file covers the provider that
loads a playbook and evaluates it, the default playbook's content, and the check that turns
an unsatisfied requirement into a block — the property the milestone is named for.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.core.config import settings
from backend.core.run_context import RunContext
from backend.invariants.playbook_rules import PlaybookError, unmet_requirements
from backend.knowledge.providers.playbook import PlaybookProvider, load_playbook
from backend.schemas.intent import IntentObject
from backend.schemas.playbook import PlaybookRequirement
from backend.workspace.models import Contract


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


# ------------------------------------------------------------------------- the default


def test_the_default_playbook_loads_and_carries_no_clause_text() -> None:
    """`load_rules` refuses a rule with prose; this proves the shipped default is clean."""
    rules = load_playbook("default")
    assert len(rules) >= 5
    # None of the shipped rules carries clause text — enforced at load, asserted here.
    assert all(r.value is None or len(r.value.split()) <= 12 for r in rules)


def test_a_missing_playbook_fails_loudly() -> None:
    """A playbook that silently does nothing is a compliance gap nobody sees."""
    load_playbook.cache_clear()
    with pytest.raises(PlaybookError, match="no playbook at"):
        load_playbook("does-not-exist")


# ------------------------------------------------------------------------- the provider


async def test_it_is_available_and_evaluates_against_intent(ctx: RunContext) -> None:
    provider = PlaybookProvider()
    intent = IntentObject(contract_type="nda", confidence=0.9, jurisdiction="IN")

    assert await provider.available(intent, ctx) is True
    contribution = await provider.contribute(intent, ctx)

    rule_ids = {r.rule_id for r in contribution.requirements}
    assert "dpdp-in" in rule_ids, "Indian jurisdiction requires DPDP data protection"
    assert "nda-confidentiality" in rule_ids
    assert "governing-law-always" in rule_ids


async def test_jurisdiction_changes_which_rules_fire(ctx: RunContext) -> None:
    provider = PlaybookProvider()

    eu = await provider.contribute(
        IntentObject(contract_type="nda", confidence=0.9, jurisdiction="DE"), ctx
    )
    india = await provider.contribute(
        IntentObject(contract_type="nda", confidence=0.9, jurisdiction="IN"), ctx
    )

    assert "gdpr-eu" in {r.rule_id for r in eu.requirements}
    assert "gdpr-eu" not in {r.rule_id for r in india.requirements}
    assert "dpdp-in" in {r.rule_id for r in india.requirements}


async def test_an_absent_fact_fires_no_rule(ctx: RunContext) -> None:
    """No jurisdiction stated → no data-protection requirement imposed.

    A rule that fired on missing data would apply a data-protection regime to a contract
    whose jurisdiction was simply never determined.
    """
    contribution = await PlaybookProvider().contribute(
        IntentObject(contract_type="nda", confidence=0.9), ctx
    )
    ids = {r.rule_id for r in contribution.requirements}
    assert "dpdp-in" not in ids
    assert "gdpr-eu" not in ids
    # The unconditional rules still fire.
    assert "governing-law-always" in ids


async def test_the_requirements_are_blocking(ctx: RunContext) -> None:
    """A playbook requirement is a gate, not advice."""
    contribution = await PlaybookProvider().contribute(
        IntentObject(contract_type="nda", confidence=0.9, jurisdiction="IN"), ctx
    )
    assert all(r.blocking for r in contribution.requirements if r.kind == "require_section")


# ------------------------------------------------------------------- the block itself


def test_a_draft_missing_a_required_section_is_unmet() -> None:
    """The property the milestone is named for: an unmet requirement is detected.

    This is what makes a playbook violation block finalization at M13 — the check that a
    required section is actually present in the draft.
    """
    requirements = (
        PlaybookRequirement(rule_id="dpdp", kind="require_section", target="data_protection"),
        PlaybookRequirement(rule_id="gl", kind="require_section", target="governing_law"),
    )
    draft = "## Confidentiality\nThe parties shall keep information secret.\n"

    unmet = unmet_requirements(draft, requirements)

    assert {r.rule_id for r in unmet} == {"dpdp", "gl"}


def test_a_draft_that_satisfies_its_requirements_is_clean() -> None:
    requirements = (
        PlaybookRequirement(rule_id="gl", kind="require_section", target="governing_law"),
    )
    draft = "## Governing Law\nThis agreement is governed by the laws of India.\n"

    assert unmet_requirements(draft, requirements) == ()


def test_the_underscored_target_matches_a_spaced_heading() -> None:
    """`data_protection` the requirement matches "Data Protection" the heading."""
    requirements = (
        PlaybookRequirement(rule_id="dp", kind="require_section", target="data_protection"),
    )
    draft = "## Data Protection\nPersonal data is processed under the DPDP Act.\n"

    assert unmet_requirements(draft, requirements) == ()


def test_only_blocking_require_section_requirements_gate() -> None:
    """A `set_value` shapes drafting; a `flag` is advisory. Neither blocks here."""
    requirements = (
        PlaybookRequirement(
            rule_id="pay", kind="set_value", target="payment_terms_days", value="45"
        ),
        PlaybookRequirement(
            rule_id="approval", kind="flag", target="legal_approval_required", blocking=False
        ),
        PlaybookRequirement(
            rule_id="advisory", kind="require_section", target="insurance", blocking=False
        ),
    )
    # An empty draft satisfies none of these, yet none blocks.
    assert unmet_requirements("", requirements) == ()


async def test_the_provider_produces_requirements_that_can_be_checked_end_to_end(
    ctx: RunContext,
) -> None:
    """The provider's requirements feed the very check that gates finalization."""
    contribution = await PlaybookProvider().contribute(
        IntentObject(contract_type="nda", confidence=0.9, jurisdiction="IN"), ctx
    )
    empty_draft = ""

    unmet = unmet_requirements(empty_draft, contribution.requirements)

    # An empty draft violates every require_section the playbook produced.
    section_reqs = [r for r in contribution.requirements if r.kind == "require_section"]
    assert {r.rule_id for r in unmet} == {r.rule_id for r in section_reqs}
