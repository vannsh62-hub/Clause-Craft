"""Drafting cannot begin without a transformation plan, and finding that out is free.

This is the proof that the system thinks in transformations rather than generations. The
guarantee is not merely that drafting fails without a plan — it is that it fails *before
spending anything*. A check inside the drafting agent would already have built and started
the agent, burning tokens, before discovering it cannot proceed. A precondition rejects the
run while it is still free.

So these tests spy on the **runtime**, not the model. "The model was never called" is too
weak — the agent could have been constructed and then errored, which still costs the build.
"The runtime was never touched" is the real property.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.artifacts import Artifact, ArtifactStore
from backend.artifacts.store import ArtifactMissing
from backend.core.config import settings
from backend.core.run_context import RunContext
from backend.phase_b import drafting as drafting_mod
from backend.phase_b.drafting import draft
from backend.runtime.adapters.openai_agents import OpenAIAgentsRuntime
from backend.schemas.cko import ContractKnowledgeObject
from backend.schemas.intent import IntentObject, ResolutionPlan
from backend.schemas.plan import SectionDecision, TransformationPlan
from backend.workspace.models import Contract
from tests.fakes import FakeModel, Turn, text_message


def _drafting_runtime(refs: tuple[str, ...] = ()) -> OpenAIAgentsRuntime:
    """A real runtime whose model returns drafted text for each requested ref."""
    payload = {"sections": [{"ref": ref, "text": f"Text for {ref}."} for ref in refs]}
    return OpenAIAgentsRuntime(FakeModel([Turn(output=[text_message(json.dumps(payload))])]))


class SpyRuntime:
    """Records whether it was asked to run anything at all.

    The point of the whole test file: if the precondition works, this is never touched, so
    `run_calls` stays 0 even though a drafting agent would have used it.
    """

    name = "spy"

    def __init__(self) -> None:
        self.run_calls = 0

    async def run(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - must not run
        self.run_calls += 1
        raise AssertionError("the runtime was invoked despite a missing transformation plan")

    async def run_many(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        self.run_calls += 1
        raise AssertionError("the runtime was invoked despite a missing transformation plan")


def _cko(contract_id: uuid.UUID) -> ContractKnowledgeObject:
    return ContractKnowledgeObject(
        contract_id=contract_id,
        resolution=ResolutionPlan(providers=("llm",)),
        intent=IntentObject(contract_type="nda", confidence=0.9),
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


# ------------------------------------------------------------------- the precondition


async def test_drafting_refuses_without_a_transformation_plan(ctx: RunContext) -> None:
    with pytest.raises(ArtifactMissing, match="06-transformation-plan.json"):
        await draft(_cko(ctx.contract_id), ctx)


async def test_the_refusal_is_free_the_runtime_is_never_touched(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The load-bearing assertion.

    Spy on the runtime, not the model. A missing plan must be discovered before any agent
    is built — otherwise the system can be talked into starting to draft and only then
    noticing it has nothing to draft toward.
    """
    spy = SpyRuntime()
    monkeypatch.setattr(drafting_mod, "RUNTIME", spy)

    with pytest.raises(ArtifactMissing):
        await draft(_cko(ctx.contract_id), ctx)

    assert spy.run_calls == 0, "the gate must reject before the runtime is reached"


async def test_drafting_proceeds_once_the_plan_is_on_disk(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(drafting_mod, "RUNTIME", _drafting_runtime())
    plan = TransformationPlan(
        keep=(SectionDecision(name="Confidentiality", decision="keep", reason="applies"),),
        modify=(SectionDecision(name="IP", decision="modify", reason="vendor licence"),),
    )
    await ArtifactStore(ctx.session_factory, ctx.contract_id).save(
        Artifact.TRANSFORMATION_PLAN, plan
    )

    result = await draft(_cko(ctx.contract_id), ctx)

    assert result.sections_touched == 2, "the plan reached the drafter intact"
    assert result.storage_key.endswith(".docx"), "a document was produced"


async def test_the_precondition_reads_the_plan_that_is_actually_there(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not a mere existence check — the drafter receives the plan's contents.

    A precondition that only checked for the file would pass on an empty or wrong file. The
    section count proves the drafter got the real decisions.
    """
    monkeypatch.setattr(drafting_mod, "RUNTIME", _drafting_runtime())
    plan = TransformationPlan(
        remove=(
            SectionDecision(name="Working hours", decision="remove", reason="employment-specific"),
            SectionDecision(
                name="Internship term", decision="remove", reason="employment-specific"
            ),
            SectionDecision(name="Probation", decision="remove", reason="employment-specific"),
        )
    )
    await ArtifactStore(ctx.session_factory, ctx.contract_id).save(
        Artifact.TRANSFORMATION_PLAN, plan
    )

    result = await draft(_cko(ctx.contract_id), ctx)

    assert result.sections_touched == 3


async def test_a_malformed_plan_is_distinguished_from_a_missing_one(ctx: RunContext) -> None:
    """Missing means "produce the plan". Malformed means the planner produced garbage and
    re-running drafting blindly will not help."""
    from backend.artifacts.store import MalformedArtifact
    from backend.workspace.store import WorkspaceStore

    async with ctx.session_factory() as s:
        await WorkspaceStore(s).write(
            ctx.contract_id, Artifact.TRANSFORMATION_PLAN.path, '{"keep": "not a list"}'
        )
        await s.commit()

    with pytest.raises(MalformedArtifact):
        await draft(_cko(ctx.contract_id), ctx)
