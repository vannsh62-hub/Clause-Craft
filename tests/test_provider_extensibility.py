"""Adding a knowledge source must not touch the pipeline.

This is the test that decides whether the provider pattern is real. Spec 05 §5 claims a
regulation database, a policy store or a company wiki can be plugged in without changing
the pipeline — and a claim like that is worth nothing unless something checks it, because
the failure mode is gradual. One `if provider == "template"` in the gather step, added for
a good reason under time pressure, and the pattern is over without anyone noticing.

So the provider below is defined **here, in the test file**, registered at runtime, and
asserted to participate fully. Nothing under `backend/phase_a/` or `backend/knowledge/`
knows it exists. If making this pass ever requires editing a pipeline module, that edit is
the finding.
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
from backend.knowledge.base import PRECEDENCE, order_by_precedence, precedence_of
from backend.knowledge.registry import (
    ProviderError,
    get_provider,
    register_provider,
    registered_providers,
    temporary_registration,
)
from backend.phase_a.gather import gather
from backend.phase_a.resolver import plan_for
from backend.schemas.cko import Provenance, RiskSignal
from backend.schemas.intent import IntentObject
from backend.schemas.playbook import PlaybookRequirement
from backend.schemas.provider import KnowledgeContribution
from backend.workspace.models import Contract

INTENT = IntentObject(contract_type="nda", confidence=0.9, jurisdiction="IN", country="IN")


class RegulationProvider:
    """A knowledge source that does not exist in the codebase.

    Modelled on the first thing spec 05 §5 says should be pluggable. It contributes a
    requirement, which is the most demanding case: requirements block finalization, so a
    provider that could not contribute one would be second-class.
    """

    name = "regulation"

    def __init__(self) -> None:
        self.calls = 0

    async def available(self, intent: IntentObject, ctx: RunContext) -> bool:
        return intent.jurisdiction == "IN"

    async def contribute(self, intent: IntentObject, ctx: RunContext) -> KnowledgeContribution:
        self.calls += 1
        return KnowledgeContribution(
            provider=self.name,
            provenance=Provenance(provider=self.name, locator="dpdp-act-2023"),
            requirements=(
                PlaybookRequirement(
                    rule_id="dpdp-s8",
                    kind="require_section",
                    target="Data Protection",
                    reason="DPDP Act 2023",
                ),
            ),
        )


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


# ------------------------------------------------------------------- the actual claim


async def test_a_new_provider_participates_with_no_pipeline_change(ctx: RunContext) -> None:
    """Defined in this file, registered at runtime, contributes to the run."""
    provider = RegulationProvider()

    with temporary_registration(provider):
        plan = await plan_for(INTENT, ctx)
        contributions = await gather(plan, INTENT, ctx)

    assert "regulation" in plan.providers
    assert provider.calls == 1

    by_provider = {c.provider: c for c in contributions}
    assert "regulation" in by_provider
    assert by_provider["regulation"].requirements[0].target == "Data Protection"


async def test_the_new_provider_loses_conflicts_to_every_known_source(ctx: RunContext) -> None:
    """An unlisted provider sorts last.

    Deliberate: an experimental source should be able to participate without editing the
    precedence policy, and should lose every conflict while it is unlisted. Silently
    outranking the playbook would be the dangerous default.
    """
    provider = RegulationProvider()

    with temporary_registration(provider):
        contributions = await gather(await plan_for(INTENT, ctx), INTENT, ctx)

    assert precedence_of("regulation") == len(PRECEDENCE)
    assert [c.provider for c in contributions][-1] == "regulation"


async def test_availability_narrows_participation(ctx: RunContext) -> None:
    """A provider with nothing to offer does not run at all."""
    provider = RegulationProvider()
    elsewhere = IntentObject(contract_type="nda", confidence=0.9, jurisdiction="DE")

    with temporary_registration(provider):
        plan = await plan_for(elsewhere, ctx)
        await gather(plan, elsewhere, ctx)

    assert "regulation" not in plan.providers
    assert provider.calls == 0


# ------------------------------------------------------------------------- the floor


async def test_the_llm_provider_is_always_available(ctx: RunContext) -> None:
    """A run with no other source must still have something to draft from."""
    plan = await plan_for(INTENT, ctx)
    assert "llm" in plan.providers

    contributions = await gather(plan, INTENT, ctx)
    assert any(c.provider == "llm" for c in contributions)


async def test_the_llm_provider_reports_only_what_intent_established(ctx: RunContext) -> None:
    """It must not invent facts about this contract.

    A guessed payment term contributed here would be recorded as knowledge, outrank
    nothing, and be indistinguishable from a fact by the time anyone read the CKO.
    """
    contributions = await gather(await plan_for(INTENT, ctx), INTENT, ctx)
    llm = next(c for c in contributions if c.provider == "llm")

    assert llm.metadata is not None
    assert llm.metadata.jurisdiction == "IN"
    assert llm.metadata.payment_terms_days is None
    assert llm.clause_candidates == ()


# ------------------------------------------------------------------------- precedence


def test_precedence_is_fixed_and_playbook_outranks_everything() -> None:
    assert PRECEDENCE[0] == "playbook"
    assert PRECEDENCE[-1] == "llm"
    assert order_by_precedence(("llm", "template", "playbook")) == (
        "playbook",
        "template",
        "llm",
    )


def test_contributions_come_back_in_precedence_order_not_completion_order() -> None:
    """Providers run concurrently, so completion order is not deterministic.

    The aggregator must see a stable sequence or its conflict resolution would depend on
    which provider happened to finish first.
    """
    assert order_by_precedence(("reference", "playbook", "llm", "clause_library")) == (
        "playbook",
        "clause_library",
        "reference",
        "llm",
    )


# --------------------------------------------------------------------------- registry


def test_a_duplicate_name_is_refused() -> None:
    """Two providers answering to one name would be resolved by import order."""
    with (
        temporary_registration(RegulationProvider()),
        pytest.raises(ProviderError, match="already registered"),
    ):
        register_provider(RegulationProvider())


def test_an_unknown_provider_is_a_clear_error() -> None:
    with pytest.raises(ProviderError, match="no knowledge provider named"):
        get_provider("company_wiki")


def test_registration_does_not_leak_between_tests() -> None:
    """`temporary_registration` exists because the registry is process-global.

    Without it a test that registers a provider changes the behaviour of every test that
    runs after it, and the failures surface in unrelated files.
    """
    assert "regulation" not in {p.name for p in registered_providers()}


async def test_a_failing_provider_degrades_the_run_rather_than_ending_it(
    ctx: RunContext,
) -> None:
    """Losing one source should cost its knowledge, not the whole run.

    Reference-document analysis is the realistic case: three documents, one unreadable.
    The right outcome is a contract drafted from the other two with the gap recorded.
    """

    class BrokenProvider:
        name = "broken"

        async def available(self, intent: IntentObject, ctx: RunContext) -> bool:
            return True

        async def contribute(self, intent: IntentObject, ctx: RunContext) -> KnowledgeContribution:
            raise RuntimeError("the document could not be read")

    with temporary_registration(BrokenProvider()):
        plan = await plan_for(INTENT, ctx)
        contributions = await gather(plan, INTENT, ctx)

    assert "broken" in plan.providers, "it was asked to participate"
    assert not any(c.provider == "broken" for c in contributions), "and contributed nothing"
    assert any(c.provider == "llm" for c in contributions), "the run continued"


def test_a_provider_without_a_name_is_refused() -> None:
    class Nameless:
        name = ""

        async def available(self, intent: IntentObject, ctx: RunContext) -> bool:
            return True

        async def contribute(self, intent: IntentObject, ctx: RunContext) -> KnowledgeContribution:
            return KnowledgeContribution(provider="", provenance=Provenance(provider=""))

    with pytest.raises(ProviderError, match="must have a name"):
        register_provider(Nameless())


def test_the_contribution_vocabulary_covers_what_providers_need() -> None:
    """A shape every provider can express itself in.

    If a source had to smuggle its knowledge through a field meant for something else, the
    uniform interface would be a fiction and the aggregator would need to special-case it.
    """
    contribution = KnowledgeContribution(
        provider="regulation",
        provenance=Provenance(provider="regulation"),
        requirements=(
            PlaybookRequirement(rule_id="r", kind="require_section", target="Data Protection"),
        ),
    )
    assert not contribution.is_empty
    assert KnowledgeContribution(provider="x", provenance=Provenance(provider="x")).is_empty


def test_risk_signals_are_expressible_by_any_provider() -> None:
    """Not used by the shipped providers yet; the vocabulary must not need widening later."""
    assert RiskSignal(category="Indemnity", level="high", message="uncapped").level == "high"
