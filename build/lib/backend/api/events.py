"""Run events: persisted first, notified second.

A dropped SSE connection must not lose a run. So the agent task never streams to a client;
it appends to `run_events` and then wakes whoever is listening. A client reconnects with
`?seq=N` and replays the rest from Postgres.

**Persist before notify.** If we notified first, a subscriber could receive event 7, reconnect
after a blip, ask for everything after 7, and never be told about 7 — because it was never
written. The order of these two lines is the durability guarantee.

`Notifier` is an interface on purpose. An in-process `asyncio.Queue` is correct while the
agent task and the SSE handler share a process. The moment they can live on different pods it
needs `LISTEN/NOTIFY` or Redis. Replay from Postgres covers the gap either way, which is why
this is a safe place to start simple.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Sequence
from typing import Protocol, cast

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.logging import get_logger
from backend.schemas.events import TERMINAL, RunEventOut, RunEventType
from backend.workspace.models import RunEvent

__all__ = ["EventPublisher", "InProcessNotifier", "Notifier", "replay", "stream_run_events"]

log = get_logger(__name__)


class Notifier(Protocol):
    def publish(self, run_id: uuid.UUID, seq: int) -> None: ...

    def subscribe(self, run_id: uuid.UUID) -> asyncio.Queue[int]: ...

    def unsubscribe(self, run_id: uuid.UUID, queue: asyncio.Queue[int]) -> None: ...


class InProcessNotifier:
    """Single-process fan-out. Never the source of truth — only a doorbell."""

    def __init__(self) -> None:
        self._subscribers: dict[uuid.UUID, set[asyncio.Queue[int]]] = defaultdict(set)

    def publish(self, run_id: uuid.UUID, seq: int) -> None:
        for queue in list(self._subscribers.get(run_id, ())):
            queue.put_nowait(seq)

    def subscribe(self, run_id: uuid.UUID) -> asyncio.Queue[int]:
        queue: asyncio.Queue[int] = asyncio.Queue()
        self._subscribers[run_id].add(queue)
        return queue

    def unsubscribe(self, run_id: uuid.UUID, queue: asyncio.Queue[int]) -> None:
        self._subscribers.get(run_id, set()).discard(queue)
        if not self._subscribers.get(run_id):
            self._subscribers.pop(run_id, None)


_NOTIFIER = InProcessNotifier()


def get_notifier() -> Notifier:
    return _NOTIFIER


class EventPublisher:
    """Appends events for one run. Exactly one publisher writes to a run at a time."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        run_id: uuid.UUID,
        notifier: Notifier | None = None,
    ) -> None:
        self._factory = session_factory
        self._run_id = run_id
        self._notifier = notifier or get_notifier()

    async def emit(self, event_type: RunEventType, **payload: object) -> int:
        async with self._factory() as session:
            # Serialise seq assignment for this run. One writer today; the lock is what makes
            # that an assumption the database enforces rather than one we merely hold.
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": str(self._run_id)}
            )
            next_seq = int(
                (
                    await session.execute(
                        select(func.coalesce(func.max(RunEvent.seq), 0) + 1).where(
                            RunEvent.run_id == self._run_id
                        )
                    )
                ).scalar_one()
            )
            session.add(
                RunEvent(run_id=self._run_id, seq=next_seq, event_type=event_type, payload=payload)
            )
            await session.commit()  # durable FIRST

        self._notifier.publish(self._run_id, next_seq)  # doorbell SECOND
        return next_seq

    async def stage(self, name: str, status: str, **detail: object) -> int:
        return await self.emit("stage", stage=name, status=status, detail=detail)


async def replay(
    session_factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID, after_seq: int = 0
) -> Sequence[RunEventOut]:
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(RunEvent)
                    .where(RunEvent.run_id == run_id, RunEvent.seq > after_seq)
                    .order_by(RunEvent.seq)
                )
            )
            .scalars()
            .all()
        )
    return [
        RunEventOut(
            seq=r.seq,
            event_type=cast(RunEventType, r.event_type),
            payload=r.payload,
            created_at=r.created_at,
        )
        for r in rows
    ]


async def stream_run_events(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    after_seq: int = 0,
    *,
    notifier: Notifier | None = None,
    idle_timeout: float = 30.0,
) -> AsyncIterator[RunEventOut]:
    """Replay everything after `after_seq`, then tail until a terminal event.

    Subscribes *before* replaying. Doing it the other way round leaves a window in which an
    event is written after the replay query and before the subscription — and is never seen.
    """
    bell = (notifier or get_notifier()).subscribe(run_id)
    last = after_seq
    try:
        for event in await replay(session_factory, run_id, last):
            last = event.seq
            yield event
            if event.event_type in TERMINAL:
                return

        while True:
            # The run may have finished in another process, or the doorbell was missed.
            # Postgres is the source of truth, so look there rather than hanging.
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(bell.get(), timeout=idle_timeout)

            for event in await replay(session_factory, run_id, last):
                last = event.seq
                yield event
                if event.event_type in TERMINAL:
                    return
    finally:
        (notifier or get_notifier()).unsubscribe(run_id, bell)
