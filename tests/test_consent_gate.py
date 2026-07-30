"""Consent before drafting a contract nothing but the model can vouch for.

The engine drafts types with no approved clauses by falling back on the model's own legal
knowledge. That is a legitimate thing to do and a bad thing to do *silently*: a user cannot
otherwise tell a draft assembled from reviewed text apart from one written from memory.
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
from backend.phase_a.consent import (
    QUESTION_NAME,
    consent_question,
    has_consent,
    is_affirmative,
    needs_consent,
    record_consent,
)
from backend.schemas.intent import IntentObject, ResolutionPlan
from backend.workspace.models import Contract


@pytest_asyncio.fixture
async def ctx() -> AsyncIterator[RunContext]:
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    cid = uuid.uuid4()
    async with factory() as s:
        s.add(Contract(id=cid, contract_type="reseller", request="draft a reseller agreement"))
        await s.commit()
    try:
        yield RunContext(contract_id=cid, session_factory=factory)
    finally:
        async with factory() as s:
            await s.execute(delete(Contract).where(Contract.id == cid))
            await s.commit()
        await engine.dispose()


# --------------------------------------------------------------------- when it is required


def test_a_run_with_no_backed_source_needs_consent() -> None:
    assert needs_consent(ResolutionPlan(providers=("playbook", "llm")))


def test_approved_clauses_mean_no_question() -> None:
    assert not needs_consent(ResolutionPlan(providers=("clause_library", "llm")))


def test_a_template_means_no_question() -> None:
    """The uploaded document is the source of the words, so nothing is unbacked."""
    assert not needs_consent(ResolutionPlan(providers=("template", "llm")))


def test_a_playbook_alone_does_not_count_as_backing() -> None:
    """A playbook contributes rules about what must be present, never clause text."""
    assert needs_consent(ResolutionPlan(providers=("playbook", "reference", "llm")))


# --------------------------------------------------------------------- reading the answer


@pytest.mark.parametrize("answer", ["yes", "Yes", "y", "ok", "proceed", "go ahead", "Sure."])
def test_an_affirmative_answer_is_recognised(answer: str) -> None:
    assert is_affirmative(answer)


@pytest.mark.parametrize(
    "answer", ["no", "No thanks", "not without review", "", "   ", "maybe later", "stop"]
)
def test_anything_that_is_not_clearly_yes_is_a_no(answer: str) -> None:
    """Reading "no, not without review" as consent is far worse than asking twice."""
    assert not is_affirmative(answer)


# ------------------------------------------------------------------------- once per contract


async def test_consent_is_remembered_so_the_resumed_run_does_not_ask_again(
    ctx: RunContext,
) -> None:
    """A suspension re-enters Phase A from the top, so the answer must outlive the slice."""
    assert not await has_consent(ctx)

    await record_consent(ctx, granted=True, answer="yes")

    assert await has_consent(ctx)


async def test_a_refusal_is_recorded_too(ctx: RunContext) -> None:
    """Recorded either way — a declined run must not re-ask on the next attempt."""
    await record_consent(ctx, granted=False, answer="no")
    assert await has_consent(ctx)


def test_the_question_names_the_contract_type_and_the_risk() -> None:
    question = consent_question(IntentObject(contract_type="reseller", confidence=0.9))

    assert question.name == QUESTION_NAME, "the resume path keys off this exact name"
    assert "reseller" in question.question
    assert "not been reviewed" in question.question
