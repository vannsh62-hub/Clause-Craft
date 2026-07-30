"""A resolution plan may narrow precedence. It may never reorder it.

Narrowing is a fact about this run: no template was uploaded, so the template provider does
not participate. Reordering would be a policy change — and the specific policy change
available is demoting the playbook below the template, which is how a compliance rule stops
applying without anyone deciding that it should.

The failure is invisible without this check. The run succeeds, a contract is produced, and
the only symptom is that the wrong source won a conflict nobody was told about. So the
ordering is enforced rather than trusted.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.artifacts import Artifact, ArtifactStore
from backend.core.config import settings
from backend.core.run_context import RunContext
from backend.knowledge.base import PRECEDENCE
from backend.knowledge.registry import temporary_registration
from backend.phase_a.resolver import ResolutionError, plan_for, resolve, validate_plan
from backend.schemas.intent import IntentObject, ResolutionPlan
from backend.schemas.provider import KnowledgeContribution
from backend.workspace.models import Contract

INTENT = IntentObject(contract_type="nda", confidence=0.9, jurisdiction="IN")


class _Provider:
    def __init__(self, name: str) -> None:
        self.name = name

    async def available(self, intent: IntentObject, ctx: RunContext) -> bool:
        return True

    async def contribute(
        self, intent: IntentObject, ctx: RunContext
    ) -> KnowledgeContribution:  # pragma: no cover
        raise NotImplementedError


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


# ------------------------------------------------------------------------- the invariant


def test_a_plan_that_reorders_precedence_is_rejected() -> None:
    """The demotion that matters: a template outranking the playbook."""
    with pytest.raises(ResolutionError, match="reorders the precedence policy"):
        validate_plan(ResolutionPlan(providers=("template", "playbook")))


def test_the_rejection_says_what_the_correct_order_would_be() -> None:
    with pytest.raises(ResolutionError, match=r"\['playbook', 'template'\]"):
        validate_plan(ResolutionPlan(providers=("template", "playbook")))


@pytest.mark.parametrize(
    "providers",
    [
        ("playbook", "clause_library", "template", "reference", "llm"),
        ("playbook", "llm"),
        ("template", "llm"),
        ("llm",),
        (),
    ],
)
def test_narrowing_is_allowed(providers: tuple[str, ...]) -> None:
    """Any subset, as long as the relative order holds."""
    validate_plan(ResolutionPlan(providers=providers))


def test_a_provider_listed_twice_is_rejected() -> None:
    """It would contribute twice and silently outweigh its own precedence."""
    with pytest.raises(ResolutionError, match="more than once"):
        validate_plan(ResolutionPlan(providers=("playbook", "llm", "llm")))


def test_an_unlisted_provider_sorts_last_and_is_still_valid() -> None:
    """An experimental source may participate without editing the policy — and loses."""
    validate_plan(ResolutionPlan(providers=("playbook", "llm", "company_wiki")))

    with pytest.raises(ResolutionError):
        validate_plan(ResolutionPlan(providers=("company_wiki", "playbook")))


# ---------------------------------------------------------------------------- resolving


async def test_registration_order_does_not_leak_into_authority(ctx: RunContext) -> None:
    """The resolver sorts by precedence; the order providers happened to register in must
    not survive into the plan.

    Uses sentinel names (`zzz-...`, `aaa-...`) rather than real provider names, so it does
    not collide with providers as they become real — an earlier version reached for
    `reference` and `clause_library`, which broke the moment those shipped. Both sentinels
    are unlisted, so they tie on precedence and fall back to the alphabetical tiebreak:
    registering `zzz` first and `aaa` second must still yield `aaa` first.
    """
    with (
        temporary_registration(_Provider("zzz-registered-first")),
        temporary_registration(_Provider("aaa-registered-second")),
    ):
        plan = await plan_for(INTENT, ctx)

    assert plan.providers.index("aaa-registered-second") < plan.providers.index(
        "zzz-registered-first"
    ), "registration order leaked into the plan"
    assert plan.providers[-1].startswith("zzz"), "unlisted providers sort after the known ones"


async def test_the_plan_produced_by_resolution_always_passes_its_own_validation(
    ctx: RunContext,
) -> None:
    with temporary_registration(_Provider("zzz-experimental")):
        validate_plan(await plan_for(INTENT, ctx))


async def test_the_resolution_artifact_is_written(ctx: RunContext) -> None:
    plan = await resolve(INTENT, ctx)

    stored = await ArtifactStore(ctx.session_factory, ctx.contract_id).load(Artifact.RESOLUTION)
    assert isinstance(stored, ResolutionPlan)
    assert stored.providers == plan.providers


async def test_the_rationale_names_the_providers_for_a_human_reader(ctx: RunContext) -> None:
    """The artifact is the explanation. "5 providers" is not one."""
    plan = await resolve(INTENT, ctx)
    assert "llm" in plan.rationale
    assert INTENT.contract_type in plan.rationale


def test_precedence_policy_is_what_the_spec_says() -> None:
    """Pinned. Changing this order changes which source wins every conflict."""
    assert PRECEDENCE == ("playbook", "clause_library", "template", "reference", "llm")
