"""Run events: durability first, delivery second.

The ordering in `EventPublisher.emit` is the whole design. If we notified before persisting, a
subscriber could see event 7, drop, reconnect asking for everything after 7, and never be told
about 7 — because it was never written.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, delete, select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.api.events import EventPublisher, InProcessNotifier, replay, stream_run_events
from backend.core.config import settings
from backend.workspace.models import Contract, Run, RunEvent


@pytest_asyncio.fixture
async def run() -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], uuid.UUID]]:
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    cid = uuid.uuid4()
    async with factory() as s:
        s.add(Contract(id=cid, request="Draft an NDA"))
        await s.flush()
        run_row = Run(contract_id=cid, status="running")
        s.add(run_row)
        await s.commit()
        run_id = run_row.id
    try:
        yield factory, run_id
    finally:
        async with factory() as s:
            await s.execute(delete(Contract).where(Contract.id == cid))
            await s.commit()
        await engine.dispose()


# ------------------------------------------------------------------ persist before notify


async def test_the_event_is_committed_to_postgres_before_anyone_is_told(
    run: tuple[async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    """The durability guarantee, checked from *inside* `publish`.

    A separate synchronous connection reads the row while the doorbell is ringing. It can only
    see committed data, so if the row is visible there, `emit` committed before it notified.
    Reverse the two lines in `EventPublisher.emit` and this goes red.
    """
    factory, run_id = run
    visible_at_publish: list[bool] = []

    sync_engine = create_engine(settings.database_url)

    class SpyNotifier(InProcessNotifier):
        def publish(self, rid: uuid.UUID, seq: int) -> None:
            with sync_engine.connect() as conn:
                count = conn.execute(
                    sa_text("SELECT count(*) FROM run_events WHERE run_id = :r AND seq = :s"),
                    {"r": str(rid), "s": seq},
                ).scalar_one()
            visible_at_publish.append(count == 1)
            super().publish(rid, seq)

    try:
        await EventPublisher(factory, run_id, SpyNotifier()).emit(
            "stage", stage="planning", status="started"
        )
    finally:
        sync_engine.dispose()

    assert visible_at_publish == [True], "publish() rang the doorbell before the row was committed"
    assert [r.seq for r in await replay(factory, run_id)] == [1]


async def test_seq_is_monotonic_and_starts_at_one(
    run: tuple[async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    factory, run_id = run
    publisher = EventPublisher(factory, run_id, InProcessNotifier())

    seqs = [await publisher.emit("stage", stage=f"s{i}", status="done") for i in range(5)]
    assert seqs == [1, 2, 3, 4, 5]


async def test_concurrent_emits_do_not_collide(
    run: tuple[async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    """One writer per run today. The advisory lock makes that an assumption the database
    enforces rather than one we merely hold."""
    factory, run_id = run
    publisher = EventPublisher(factory, run_id, InProcessNotifier())

    await asyncio.gather(*(publisher.emit("tool_call", tool=f"t{i}") for i in range(20)))

    async with factory() as s:
        rows = (await s.execute(select(RunEvent).where(RunEvent.run_id == run_id))).scalars().all()
    assert sorted(r.seq for r in rows) == list(range(1, 21))


# ---------------------------------------------------------------------------- replay


async def test_replay_returns_only_events_after_seq(
    run: tuple[async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    factory, run_id = run
    publisher = EventPublisher(factory, run_id, InProcessNotifier())
    for i in range(5):
        await publisher.emit("tool_call", tool=f"t{i}")

    assert [e.seq for e in await replay(factory, run_id, after_seq=0)] == [1, 2, 3, 4, 5]
    assert [e.seq for e in await replay(factory, run_id, after_seq=3)] == [4, 5]
    assert await replay(factory, run_id, after_seq=5) == []


async def test_a_client_that_reconnects_at_seq_misses_nothing(
    run: tuple[async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    factory, run_id = run
    notifier = InProcessNotifier()
    publisher = EventPublisher(factory, run_id, notifier)

    for i in range(3):
        await publisher.emit("tool_call", tool=f"t{i}")
    await publisher.emit("complete", status="complete")

    # First client drops after two events.
    got: list[int] = []
    async for event in stream_run_events(factory, run_id, 0, notifier=notifier):
        got.append(event.seq)
        if len(got) == 2:
            break
    assert got == [1, 2]

    # It reconnects with the last seq it saw.
    rest = [e.seq async for e in stream_run_events(factory, run_id, 2, notifier=notifier)]
    assert rest == [3, 4]
    assert got + rest == [1, 2, 3, 4], "no gap, no duplicate"


async def test_the_stream_stops_at_a_terminal_event(
    run: tuple[async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    factory, run_id = run
    notifier = InProcessNotifier()
    publisher = EventPublisher(factory, run_id, notifier)

    await publisher.emit("stage", stage="planning", status="done")
    await publisher.emit("input_required", questions=[{"name": "effective_date"}])
    await publisher.emit("tool_call", tool="should_never_be_streamed")

    seen = [e.event_type async for e in stream_run_events(factory, run_id, 0, notifier=notifier)]
    assert seen == ["stage", "input_required"]


async def test_a_late_subscriber_still_learns_how_the_run_ended(
    run: tuple[async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    """The run finished before anyone connected. Postgres, not the doorbell, is the truth."""
    factory, run_id = run
    notifier = InProcessNotifier()
    publisher = EventPublisher(factory, run_id, notifier)
    await publisher.emit("complete", status="complete", message="done")

    events = [e async for e in stream_run_events(factory, run_id, 0, notifier=notifier)]
    assert [e.event_type for e in events] == ["complete"]
    assert events[0].payload["message"] == "done"


# ------------------------------------------------------------------- live tailing


async def test_the_stream_delivers_events_published_after_it_subscribed(
    run: tuple[async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    factory, run_id = run
    notifier = InProcessNotifier()
    publisher = EventPublisher(factory, run_id, notifier)

    async def produce() -> None:
        await asyncio.sleep(0.05)
        await publisher.emit("tool_call", tool="render_clauses")
        await publisher.emit("complete", status="complete")

    task = asyncio.create_task(produce())
    seen = [
        e.event_type
        async for e in stream_run_events(factory, run_id, 0, notifier=notifier, idle_timeout=2.0)
    ]
    await task

    assert seen == ["tool_call", "complete"]


async def test_the_stream_survives_a_missed_doorbell(
    run: tuple[async_sessionmaker[AsyncSession], uuid.UUID],
) -> None:
    """A notifier that drops every notification. The idle poll falls back to Postgres, which
    is what makes a second process — or a lost wakeup — survivable."""
    factory, run_id = run

    class DeafNotifier(InProcessNotifier):
        def publish(self, rid: uuid.UUID, seq: int) -> None:
            pass  # the doorbell is broken

    notifier = DeafNotifier()
    await EventPublisher(factory, run_id, notifier).emit("complete", status="complete")

    seen = [
        e.event_type
        async for e in stream_run_events(factory, run_id, 0, notifier=notifier, idle_timeout=0.1)
    ]
    assert seen == ["complete"]


def test_unsubscribe_does_not_leak_queues() -> None:
    notifier = InProcessNotifier()
    run_id = uuid.uuid4()

    queue = notifier.subscribe(run_id)
    assert notifier._subscribers[run_id] == {queue}

    notifier.unsubscribe(run_id, queue)
    assert run_id not in notifier._subscribers


async def test_publishing_to_nobody_is_harmless() -> None:
    InProcessNotifier().publish(uuid.uuid4(), 1)


@pytest.mark.parametrize("event_type", ["complete", "input_required", "error"])
async def test_every_terminal_event_ends_the_stream(
    run: tuple[async_sessionmaker[AsyncSession], uuid.UUID], event_type: str
) -> None:
    factory, run_id = run
    notifier = InProcessNotifier()
    publisher = EventPublisher(factory, run_id, notifier)

    await publisher.emit(event_type)  # type: ignore[arg-type]
    await publisher.emit("tool_call", tool="after_the_end")

    seen = [e.event_type async for e in stream_run_events(factory, run_id, 0, notifier=notifier)]
    assert seen == [event_type]
