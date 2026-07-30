"""Concurrent workspace writes.

These cannot use the rolled-back `session` fixture: the point is real, separate
transactions racing each other, so they commit and clean up after themselves.

Two writers collide in practice when the SDK issues parallel tool calls in one turn, or
when an orchestrator write races a sub-agent write. `pg_advisory_xact_lock` serialises them
per contract; `UNIQUE(contract_id, path)` catches anything that slips through.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from backend.core.config import settings
from backend.workspace.models import Contract, WorkspaceFile
from backend.workspace.store import WorkspaceStore

WRITERS = 20


@pytest_asyncio.fixture
async def committed_contract() -> AsyncIterator[tuple[AsyncEngine, uuid.UUID]]:
    engine = create_async_engine(settings.database_url)
    cid = uuid.uuid4()

    async with AsyncSession(engine) as s:
        s.add(Contract(id=cid, contract_type="nda", request="concurrency"))
        await s.commit()
    try:
        yield engine, cid
    finally:
        async with AsyncSession(engine) as s:
            await s.execute(delete(Contract).where(Contract.id == cid))
            await s.commit()
        await engine.dispose()


async def _write(engine: AsyncEngine, cid: uuid.UUID, path: str, content: str) -> None:
    async with AsyncSession(engine) as s:
        await WorkspaceStore(s).write(cid, path, content)
        await s.commit()


async def test_concurrent_writes_to_one_path_do_not_race(
    committed_contract: tuple[AsyncEngine, uuid.UUID],
) -> None:
    engine, cid = committed_contract

    await asyncio.gather(*(_write(engine, cid, "plan.md", f"writer-{i}") for i in range(WRITERS)))

    async with AsyncSession(engine) as s:
        rows = (
            (await s.execute(select(WorkspaceFile).where(WorkspaceFile.contract_id == cid)))
            .scalars()
            .all()
        )

    assert len(rows) == 1, "UNIQUE(contract_id, path) must prevent a duplicate row"
    assert rows[0].version == WRITERS, (
        f"expected {WRITERS} serialised writes, saw version {rows[0].version}. "
        "A lost update means the advisory lock is not held."
    )


async def test_concurrent_writes_to_different_paths_all_land(
    committed_contract: tuple[AsyncEngine, uuid.UUID],
) -> None:
    engine, cid = committed_contract

    await asyncio.gather(*(_write(engine, cid, f"draft_v{i}.md", str(i)) for i in range(WRITERS)))

    async with AsyncSession(engine) as s:
        rows = (
            (await s.execute(select(WorkspaceFile).where(WorkspaceFile.contract_id == cid)))
            .scalars()
            .all()
        )

    assert len(rows) == WRITERS
    assert all(r.version == 1 for r in rows)


async def test_writes_to_different_contracts_are_not_serialised_against_each_other(
    committed_contract: tuple[AsyncEngine, uuid.UUID],
) -> None:
    """The lock key is the contract, not the table. Two contracts must not block each other."""
    engine, cid_a = committed_contract
    cid_b = uuid.uuid4()

    async with AsyncSession(engine) as s:
        s.add(Contract(id=cid_b, contract_type="service", request="other"))
        await s.commit()

    try:
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    *(_write(engine, cid_a, "plan.md", f"a{i}") for i in range(WRITERS)),
                    *(_write(engine, cid_b, "plan.md", f"b{i}") for i in range(WRITERS)),
                ),
                timeout=20,
            )
        # On Python 3.10 asyncio.TimeoutError is NOT the builtin TimeoutError; they only
        # became aliases in 3.11. Catching the builtin here would silently never fire.
        except asyncio.TimeoutError:  # pragma: no cover
            pytest.fail("writes to distinct contracts deadlocked or blocked on one another")

        async with AsyncSession(engine) as s:
            for cid in (cid_a, cid_b):
                row = (
                    await s.execute(select(WorkspaceFile).where(WorkspaceFile.contract_id == cid))
                ).scalar_one()
                assert row.version == WRITERS
    finally:
        async with AsyncSession(engine) as s:
            await s.execute(delete(Contract).where(Contract.id == cid_b))
            await s.commit()
