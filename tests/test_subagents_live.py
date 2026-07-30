"""Live model calls. Excluded from the default run; `pytest -m requires_api_key`.

These exercise what a fake cannot: that the real models accept our tool schemas, that
structured output round-trips into `JudgeVerdict`, and that the full suspend/resume loop
survives a real orchestrator deciding for itself what to do.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from agents.extensions.memory import SQLAlchemySession
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.core.config import settings
from backend.core.run_context import RunContext
from backend.runtime.adapters.openai_agents import runtime
from backend.subagents.judge.judge_agent import build_judge_spec
from backend.subagents.orchestrator import deep_agent
from backend.workspace.models import Contract, ContractVersion
from backend.workspace.store import WorkspaceStore

pytestmark = [
    pytest.mark.requires_api_key,
    pytest.mark.skipif(
        settings.openai_api_key.startswith("sk-test-"),
        reason="needs a real OPENAI_API_KEY in .env",
    ),
]

SLOPPY_DRAFT = """# non disclosure agreement

this Agreement is between ABC Pvt Ltd ("Discloser") and XYZ Pvt Ltd.

## Confidentiality

XYZ shall keep stuff confidential. We think you'll agree this is pretty reasonable!

### Term

The Company shall not disclose for 3 years. ABC Private Limited may terminate anytime.
"""


@pytest_asyncio.fixture
async def db() -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker[AsyncSession], uuid.UUID]]:
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    cid = uuid.uuid4()
    async with factory() as s:
        s.add(Contract(id=cid, contract_type="nda", request="Draft an NDA"))
        await s.commit()
    try:
        yield engine, factory, cid
    finally:
        async with factory() as s:
            await s.execute(delete(Contract).where(Contract.id == cid))
            await s.commit()
        await SQLAlchemySession(str(cid), engine=engine, create_tables=True).clear_session()
        await engine.dispose()


async def test_the_real_judge_subagent_scores_prose_and_returns_structured_output(
    db: tuple[AsyncEngine, async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    """Straight at the sub-agent, bypassing the composite: the deterministic gates would
    short-circuit this draft before a token was spent, which is the right behaviour and the
    wrong thing to test here."""
    _, factory, cid = db
    ctx = RunContext(contract_id=cid, session_factory=factory, contract_type="nda")

    async with factory() as s:
        await WorkspaceStore(s).write(cid, "draft_v1.md", SLOPPY_DRAFT)
        await s.commit()

    result = await runtime.run(build_judge_spec(), ctx, "Review the draft at `draft_v1.md`.")
    verdict = result.output
    assert verdict is not None

    assert 0 <= verdict.points < 30, "a real judge should not award full marks to this draft"
    assert verdict.findings, "it should say what is wrong"
    assert {f.dimension for f in verdict.findings} <= {"consistency", "formatting", "tone"}
    assert ctx.total_tokens > 0 and ctx.model_requests >= 1


async def test_the_real_orchestrator_asks_before_it_drafts(
    db: tuple[AsyncEngine, async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    """The single most important behaviour in the product: it does not invent a date.

    A free-text request that omits the effective date and the term must produce questions,
    not a draft.
    """
    engine, factory, cid = db

    outcome = await deep_agent.start_run(
        cid, "Draft an NDA between ABC Pvt Ltd and XYZ Pvt Ltd.", engine, factory
    )

    assert outcome.status == "awaiting_input", outcome.message
    assert outcome.questions, "it must ask rather than guess"

    asked = " ".join(str(q) for q in outcome.questions).lower()
    assert "date" in asked or "duration" in asked or "term" in asked

    async with factory() as s:
        drafted = (
            (await s.execute(select(ContractVersion).where(ContractVersion.contract_id == cid)))
            .scalars()
            .all()
        )
    assert drafted == [], "no draft may exist before the questions are answered"


async def test_the_real_orchestrator_declines_an_unsupported_contract_type(
    db: tuple[AsyncEngine, async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    """There is no approved clause set for an employment agreement. It must say so, not
    improvise one from memory."""
    engine, factory, cid = db

    outcome = await deep_agent.start_run(
        cid, "Draft me an employment agreement for a software engineer.", engine, factory
    )

    assert outcome.status == "complete", outcome.message
    assert "employment" in outcome.message.lower()

    async with factory() as s:
        drafted = (
            (await s.execute(select(ContractVersion).where(ContractVersion.contract_id == cid)))
            .scalars()
            .all()
        )
    assert drafted == [], "it must not have drafted an unsupported contract"
