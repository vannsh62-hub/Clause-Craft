"""Database fixtures.

Requires the compose Postgres: `docker compose up -d postgres`.

Each test runs inside a transaction that is rolled back, so tests share one schema without
sharing state. The session joins the outer transaction via a savepoint, which means store
code may call `flush()` (and even `commit()`) without escaping the rollback.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from backend.core.config import settings
from backend.core.database import Base
from backend.core.sdk import configure_sdk
from backend.workspace.models import Contract

# Tests never export traces. Also silences the SDK's "No span to add error to" warnings.
configure_sdk()

pytest_plugins: list[str] = []


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    """Create tables once for the whole session. Mirrors `alembic upgrade head`."""
    engine = create_engine(settings.database_url)
    Base.metadata.create_all(engine)
    engine.dispose()
    yield


@pytest.fixture(scope="session", autouse=True)
def _blob_storage(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Send generated documents to a temporary directory for the whole session.

    Blobs are the one thing tests write that the per-test transaction rollback cannot
    undo — `storage/generated` is a real directory on disk, and every run of the export
    and template tests left DOCX files in it that nothing ever removed. Git-ignored, so
    invisible, and unbounded.

    `get_storage()` reads `settings.storage_dir` on every call, so redirecting the setting
    catches writers and readers alike.
    """
    original = settings.storage_dir
    settings.storage_dir = str(tmp_path_factory.mktemp("blobs"))
    try:
        yield
    finally:
        settings.storage_dir = original


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as conn:
        outer = await conn.begin()
        db = AsyncSession(bind=conn, join_transaction_mode="create_savepoint")
        try:
            yield db
        finally:
            await db.close()
            await outer.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def contract_id(session: AsyncSession) -> uuid.UUID:
    contract = Contract(id=uuid.uuid4(), contract_type="nda", request="Draft an NDA")
    session.add(contract)
    await session.flush()
    return contract.id


@pytest_asyncio.fixture
async def other_contract_id(session: AsyncSession) -> uuid.UUID:
    contract = Contract(id=uuid.uuid4(), contract_type="service", request="Draft an MSA")
    session.add(contract)
    await session.flush()
    return contract.id
