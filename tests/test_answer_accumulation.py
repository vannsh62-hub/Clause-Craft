"""Answers must accumulate, and the asking must terminate.

The defect this pins was a live infinite loop: seven rounds of questions, every one answered,
and the run still asking for the uptime target. Three causes compounded.

1. Only the round just answered was folded back into the request — `contract.request` is the
   *original* text, so every earlier answer was discarded on each resume.
2. Questions are named positionally (`clarification_1`), so the same key meant a different
   question in every round.
3. The answer was handed back without its question, as `{"clarification_1": "3 years"}`,
   which tells a model nothing.

So the agent re-derived intent from the original request plus a few unlabelled values, found
the operative terms still missing, and asked again. Forever.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.api.pipeline_adapter import MAX_ASK_ROUNDS, _answer_transcript, _fold
from backend.core.config import settings
from backend.workspace.models import Contract, PendingQuestion

ORIGINAL = "Draft an SLA between NOVA Techset and Katalyst."


@pytest_asyncio.fixture
async def factory() -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], uuid.UUID]]:
    engine = create_async_engine(settings.database_url)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    cid = uuid.uuid4()
    async with maker() as s:
        s.add(Contract(id=cid, request=ORIGINAL))
        await s.commit()
    try:
        yield maker, cid
    finally:
        async with maker() as s:
            await s.execute(delete(Contract).where(Contract.id == cid))
            await s.commit()
        await engine.dispose()


async def _round(
    maker: async_sessionmaker[AsyncSession],
    cid: uuid.UUID,
    question: str,
    answer: str,
    call_id: str,
) -> None:
    """One asked-and-answered round, exactly as the gateway and the resume path record it."""
    from sqlalchemy import func as sa_func

    async with maker() as s:
        s.add(
            PendingQuestion(
                contract_id=cid,
                call_id=call_id,
                questions=[{"name": "clarification_1", "question": question, "type": "text"}],
                answers={"clarification_1": answer},
                answered_at=sa_func.now(),
            )
        )
        await s.commit()


async def test_every_earlier_answer_survives_the_next_resume(
    factory: tuple[async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    """The loop's engine: round three used to be folded in alone, losing rounds one and two."""
    maker, cid = factory
    await _round(maker, cid, "What is the term?", "3 years", "c1")
    await _round(maker, cid, "What is the uptime target?", "99.9%", "c2")
    await _round(maker, cid, "What is the response time?", "2 hours", "c3")

    async with maker() as s:
        transcript, rounds = await _answer_transcript(s, cid)

    assert rounds == 3
    folded = _fold(ORIGINAL, transcript, rounds)
    for fact in ("3 years", "99.9%", "2 hours"):
        assert fact in folded, f"{fact} was answered and must not be lost"


async def test_each_answer_is_paired_with_the_question_it_answers(
    factory: tuple[async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    """`{"clarification_1": "3 years"}` is not usable; the question text is what makes it so."""
    maker, cid = factory
    await _round(maker, cid, "What is the term?", "3 years", "c1")

    async with maker() as s:
        transcript, _ = await _answer_transcript(s, cid)

    assert transcript == ["- What is the term?\n  ANSWER: 3 years"]


async def test_the_fold_tells_the_agent_not_to_ask_again(
    factory: tuple[async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    maker, cid = factory
    await _round(maker, cid, "What is the term?", "3 years", "c1")

    async with maker() as s:
        transcript, rounds = await _answer_transcript(s, cid)

    assert "do not ask any of them again" in _fold(ORIGINAL, transcript, rounds)


async def test_asking_is_capped_so_a_loop_cannot_run_forever(
    factory: tuple[async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    """The last line of defence: even a re-asking agent must be made to draft eventually."""
    maker, cid = factory
    for n in range(MAX_ASK_ROUNDS):
        await _round(maker, cid, f"Question {n}?", f"answer {n}", f"c{n}")

    async with maker() as s:
        transcript, rounds = await _answer_transcript(s, cid)
    folded = _fold(ORIGINAL, transcript, rounds)

    assert "Ask nothing further" in folded
    assert "refers to the relevant schedule" in folded, "and it must say what to do instead"


async def test_below_the_cap_no_such_instruction_appears(
    factory: tuple[async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    """A first, legitimate clarification must not be told to stop asking."""
    maker, cid = factory
    await _round(maker, cid, "What is the term?", "3 years", "c1")

    async with maker() as s:
        transcript, rounds = await _answer_transcript(s, cid)

    assert "Ask nothing further" not in _fold(ORIGINAL, transcript, rounds)


async def test_a_blank_answer_is_not_presented_as_a_fact(
    factory: tuple[async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    """A skipped question is still unanswered; claiming otherwise invents a fact."""
    maker, cid = factory
    await _round(maker, cid, "What is the term?", "   ", "c1")

    async with maker() as s:
        transcript, _ = await _answer_transcript(s, cid)

    assert transcript == []


def test_an_unanswered_first_run_folds_to_the_original_request() -> None:
    assert _fold(ORIGINAL, [], 0) == ORIGINAL
