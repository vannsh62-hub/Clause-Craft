"""The attempt ledger.

`contract_versions` is append-only and is the single source of truth for how many drafting
attempts a contract has had. `RunContext.draft_attempts` is a cache of `count(*)` here.

This matters because `ask_user` ends one run slice and `POST /answers` starts another, each
with a fresh `RunContext`. A counter that lived only in memory would reset on every answer,
and a user could buy unlimited drafting attempts simply by being asked a question.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.run_context import RunContext
from backend.workspace.models import Contract, ContractVersion, PendingQuestion

__all__ = [
    "count_answered_ask_rounds",
    "count_attempts",
    "latest_version",
    "rehydrate_counters",
    "spent_tokens",
]


async def count_answered_ask_rounds(session: AsyncSession, contract_id: uuid.UUID) -> int:
    """How many times this contract has already stopped to ask, and been answered.

    Part of the ledger for the same reason the attempt count is: a suspension ends the run
    slice, so nothing in memory survives to say "we have asked three times already". Without
    a durable count, an agent that keeps reporting low confidence asks forever and the user
    answers forever.
    """
    stmt = (
        select(func.count())
        .select_from(PendingQuestion)
        .where(
            PendingQuestion.contract_id == contract_id,
            PendingQuestion.answered_at.is_not(None),
        )
    )
    return int((await session.execute(stmt)).scalar_one())


async def count_attempts(session: AsyncSession, contract_id: uuid.UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(ContractVersion)
        .where(ContractVersion.contract_id == contract_id)
    )
    return int((await session.execute(stmt)).scalar_one())


async def spent_tokens(session: AsyncSession, contract_id: uuid.UUID) -> tuple[int, int]:
    stmt = select(
        func.coalesce(func.sum(ContractVersion.input_tokens), 0),
        func.coalesce(func.sum(ContractVersion.output_tokens), 0),
    ).where(ContractVersion.contract_id == contract_id)
    row = (await session.execute(stmt)).one()
    return int(row[0]), int(row[1])


async def latest_version(session: AsyncSession, contract_id: uuid.UUID) -> ContractVersion | None:
    stmt = (
        select(ContractVersion)
        .where(ContractVersion.contract_id == contract_id)
        .order_by(ContractVersion.attempt.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def rehydrate_counters(context: RunContext) -> None:
    """Rebuild the in-memory state from Postgres at the start of a run slice.

    Without this, suspend/resume grants a fresh attempt budget and a zeroed token ceiling.
    In-memory is a cache; the database is the ledger.

    `contract_type` is restored here too. It is decided once, when `render_clauses` commits to
    a clause set, and both `validate_draft` and the judge need it to know what to require —
    but a resumed slice builds a fresh `RunContext` that has never seen it.
    """
    async with context.session_factory() as session:
        context.draft_attempts = await count_attempts(session, context.contract_id)
        context.input_tokens, context.output_tokens = await spent_tokens(
            session, context.contract_id
        )
        contract = await session.get(Contract, context.contract_id)
        if contract is not None and contract.contract_type:
            context.contract_type = contract.contract_type
            context.jurisdiction = contract.jurisdiction

    context.tool_call_log.clear()  # a resume is genuine new intent, not a loop
