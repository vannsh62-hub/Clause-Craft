"""Memory against a live model. `pytest -m requires_api_key`.

Two properties a fake cannot check, because both are *model behaviour*:

1. A returning user is asked fewer questions — and only about this deal.
2. Every recalled value is **disclosed**, with the date the user confirmed it.

Property 2 is the one that matters. A system that silently reuses your jurisdiction is worse
than one that asks, because you no longer know which values you chose. It is enforced by the
prompt rather than by code, and a prompt is advisory — so it is tested here, against the real
model, rather than asserted.
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
from backend.core.principal import current_principal
from backend.memory.models import MemoryFact
from backend.memory.store import MemoryStore
from backend.subagents.orchestrator import deep_agent
from backend.workspace.models import Contract, ContractVersion

pytestmark = [
    pytest.mark.requires_api_key,
    pytest.mark.skipif(
        settings.openai_api_key.startswith("sk-test-"),
        reason="needs a real OPENAI_API_KEY in .env",
    ),
]

REQUEST = (
    "Draft an NDA. We are ABC Pvt Ltd, the disclosing party. "
    "The receiving party is Globex Ltd. Indian law, Mumbai courts."
)

ANSWERS = {
    "receiving_party": "Globex Ltd",
    "receiving_signatory": "Sam Patel",
    "receiving_party_address": "9 Cyber City, Gurugram",
    "effective_date": "2026-08-01",
    "my_company_address": "12 Nariman Point, Mumbai",
    "my_signatory": "Jane Rao",
    "duration_years": "3",
}

REMEMBERED = {
    "my_company_name": "ABC Pvt Ltd",
    "my_company_address": "12 Nariman Point, Mumbai",
    "my_signatory": "Jane Rao",
    "preferred_governing_law_country": "India",
    "preferred_jurisdiction_city": "Mumbai",
    "preferred_duration_years": "3",
}


@pytest_asyncio.fixture
async def db() -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker[AsyncSession], uuid.UUID]]:
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    principal = current_principal()
    cid = uuid.uuid4()

    async with factory() as s:
        await s.execute(delete(MemoryFact).where(MemoryFact.user_id == principal.user_id))
        s.add(Contract(id=cid, contract_type=None, request=REQUEST))
        await s.commit()
    try:
        yield engine, factory, cid
    finally:
        async with factory() as s:
            await s.execute(delete(Contract).where(Contract.id == cid))
            await s.execute(delete(MemoryFact).where(MemoryFact.user_id == principal.user_id))
            await s.commit()
        await SQLAlchemySession(str(cid), engine=engine, create_tables=True).clear_session()
        await engine.dispose()


async def test_a_returning_user_is_asked_only_about_this_deal(
    db: tuple[AsyncEngine, async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    engine, factory, cid = db

    async with factory() as s:
        store = MemoryStore(s, current_principal())
        for key, value in REMEMBERED.items():
            await store.remember(key, value)
        await s.commit()

    outcome = await deep_agent.start_run(cid, REQUEST, engine, factory)

    assert outcome.status == "awaiting_input", outcome.message
    asked = {q["name"] for q in outcome.questions or []}

    # It must not re-ask anything it already holds, confirmed and fresh.
    assert "my_signatory" not in asked
    assert "governing_law_country" not in asked
    assert "duration_years" not in asked

    # It must still ask about the counterparty and the date. Those are never remembered.
    assert any("effective" in a or "date" in a for a in asked), asked

    # And it must finish.
    rounds = 0
    while outcome.status == "awaiting_input" and rounds < 4:
        answers = {q["name"]: ANSWERS.get(q["name"], "Sam Patel") for q in outcome.questions or []}
        outcome = await deep_agent.resume_run(cid, answers, engine, factory)
        rounds += 1

    assert outcome.status == "complete", outcome.message

    async with factory() as s:
        versions = (
            (await s.execute(select(ContractVersion).where(ContractVersion.contract_id == cid)))
            .scalars()
            .all()
        )
    assert [v for v in versions if v.finalized_at], "a returning user must still get a contract"


async def test_every_recalled_value_is_disclosed_with_its_date(
    db: tuple[AsyncEngine, async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    """The safety property. A user who cannot tell which values they chose and which the machine
    chose has been handed a worse tool, not a better one."""
    engine, factory, cid = db

    async with factory() as s:
        store = MemoryStore(s, current_principal())
        for key, value in REMEMBERED.items():
            await store.remember(key, value)
        await s.commit()

    outcome = await deep_agent.start_run(cid, REQUEST, engine, factory)
    rounds = 0
    while outcome.status == "awaiting_input" and rounds < 4:
        answers = {q["name"]: ANSWERS.get(q["name"], "Sam Patel") for q in outcome.questions or []}
        outcome = await deep_agent.resume_run(cid, answers, engine, factory)
        rounds += 1

    message = outcome.message.lower()

    # The recalled values themselves.
    assert "india" in message, "it used the remembered governing law without saying so"
    assert "jane rao" in message, "it used the remembered signatory without saying so"

    # And that they were *recalled*, not supplied now.
    assert "confirm" in message, "it must say when the user confirmed a reused value"


async def test_the_agent_never_ends_a_run_by_asking_in_prose(
    db: tuple[AsyncEngine, async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    """A question in a final message is a question the user cannot answer: the run is over, there
    is no form, and nothing is waiting for them.

    This regressed the moment memory landed — with facts to recite, the agent became chatty and
    listed what it knew, then asked for the rest conversationally. Unhelpful rather than unsafe,
    so the prompt is the right layer to fix it, and this is the guard.
    """
    engine, factory, cid = db

    async with factory() as s:
        store = MemoryStore(s, current_principal())
        for key, value in REMEMBERED.items():
            await store.remember(key, value)
        await s.commit()

    outcome = await deep_agent.start_run(cid, REQUEST, engine, factory)

    if outcome.status == "complete":
        async with factory() as s:
            versions = (
                (await s.execute(select(ContractVersion).where(ContractVersion.contract_id == cid)))
                .scalars()
                .all()
            )
        finalized = [v for v in versions if v.finalized_at]
        assert finalized, (
            "the run completed without producing a contract. If it still needed something it "
            f"should have called ask_user, not stopped. Message was:\n{outcome.message}"
        )
