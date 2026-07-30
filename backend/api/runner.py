"""Starting an agent run in the background.

`POST /contracts` returns `202` immediately. A draft takes half a minute across several named
stages, and blocking an HTTP request on that is both poor UX and fragile: a proxy timeout, a
refreshed tab, or a lost connection would abandon a run that is spending money.

The run task owns the `run` row and the event stream. It never touches the response.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from sqlalchemy import func as sa_func

from backend.api.deps import get_engine, get_session_factory
from backend.api.events import EventPublisher
from backend.api.pipeline_adapter import SliceOutcome, resume_run, start_run
from backend.core.logging import get_logger
from backend.workspace.models import Run

__all__ = ["launch_answer_run", "launch_first_run", "new_run"]

log = get_logger(__name__)

_STATUS = {
    "complete": "complete",
    "awaiting_input": "suspended",
    "turns_exhausted": "failed",
    "over_budget": "failed",
}

#: Tasks are held so the event loop does not garbage-collect a running coroutine.
_TASKS: set[asyncio.Task[None]] = set()


async def new_run(contract_id: uuid.UUID) -> uuid.UUID:
    async with get_session_factory()() as session:
        run = Run(contract_id=contract_id, status="running")
        session.add(run)
        await session.commit()
        return run.id


async def _finish_run(run_id: uuid.UUID, status: str, error: str | None = None) -> None:
    async with get_session_factory()() as session:
        run = await session.get(Run, run_id)
        if run is not None:
            run.status = status
            run.error = error
            run.finished_at = sa_func.now()
            await session.commit()


async def _drive(
    run_id: uuid.UUID, slice_fn: Callable[[EventPublisher], Awaitable[SliceOutcome]]
) -> None:
    publisher = EventPublisher(get_session_factory(), run_id)
    try:
        outcome = await slice_fn(publisher)
    except Exception as exc:  # already emitted as an `error` event by the slice
        log.exception("run %s failed", run_id)
        await _finish_run(run_id, "failed", type(exc).__name__)
        return
    await _finish_run(run_id, _STATUS[outcome.status])


def _spawn(coro: Coroutine[Any, Any, None]) -> None:
    task: asyncio.Task[None] = asyncio.create_task(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


def launch_first_run(contract_id: uuid.UUID, run_id: uuid.UUID, request: str) -> None:
    async def go(publisher: EventPublisher) -> SliceOutcome:
        await publisher.stage("planning", "started")
        return await start_run(
            contract_id, request, get_engine(), get_session_factory(), publisher=publisher
        )

    _spawn(_drive(run_id, go))


def launch_answer_run(contract_id: uuid.UUID, run_id: uuid.UUID, answers: dict[str, str]) -> None:
    async def go(publisher: EventPublisher) -> SliceOutcome:
        await publisher.stage("resuming", "started")
        return await resume_run(
            contract_id, answers, get_engine(), get_session_factory(), publisher=publisher
        )

    _spawn(_drive(run_id, go))
