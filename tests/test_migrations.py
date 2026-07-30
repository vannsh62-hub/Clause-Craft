"""The migration chain must actually run, and must agree with the models.

`tests/conftest.py` builds the schema with `Base.metadata.create_all`, and its docstring
claims that "mirrors `alembic upgrade head`". It does not. `create_all` reads the models
and never opens the migrations, so the two can disagree indefinitely with every test
still green. They did, twice, at the same time:

- Two revisions branched off `a4edecc1901f`, so `alembic upgrade head` failed outright
  with "Multiple head revisions are present". CI ran that command and went red; no test
  did, so nothing pointed at the cause.
- `extracted_clause_matches.score` was `Integer` in the model and `Numeric` in the
  migration, under a `Mapped[float]` annotation. Three declarations, no agreement, and
  fractional scores silently truncated to 0 on write.

`test_single_head` catches the first class and needs no database. The other two catch the
second class and need the compose Postgres, like the rest of the suite.

Migrations run in a subprocess against a throwaway database: `alembic/env.py` overwrites
`sqlalchemy.url` from `settings` on import, so redirecting it in-process would mean
mutating a module-level singleton, and the developer's own dev database is not a
reasonable thing for a test to migrate up and down.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

import backend.memory.models  # noqa: F401  (registers tables on Base.metadata)
import backend.workspace.models  # noqa: F401  (ditto)
from backend.core.config import settings
from backend.core.database import Base

REPO_ROOT = Path(__file__).resolve().parent.parent

# Owned by the OpenAI Agents SDK (`SQLAlchemySession`), not by Base.metadata. `env.py`
# filters these from autogenerate for the same reason; the comparison must agree with it.
SDK_OWNED_TABLES = frozenset({"agent_sessions", "agent_messages"})


def _alembic_config() -> Config:
    return Config(str(REPO_ROOT / "alembic.ini"))


def _url_for(database: str) -> str:
    """Rewrite the configured URL to point at a different database on the same server."""
    parts = urlsplit(settings.database_url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


def _run_alembic(*args: str, url: str) -> None:
    """Run alembic in a subprocess, exactly as CI does."""
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")


def test_single_head() -> None:
    """Exactly one head.

    A branched graph makes `alembic upgrade head` fail, which is a broken deploy and a
    red CI run. Two revisions authored in parallel against the same parent is the normal
    way this happens, and it is invisible until someone runs the command.
    """
    heads = ScriptDirectory.from_config(_alembic_config()).get_heads()
    assert len(heads) == 1, (
        f"{len(heads)} migration heads: {sorted(heads)}. "
        "Merge them with `alembic merge -m '<why>' " + " ".join(sorted(heads)) + "`."
    )


@pytest.fixture(scope="module")
def migrated_url() -> Iterator[str]:
    """A throwaway database with the full migration chain applied."""
    name = f"migration_check_{uuid.uuid4().hex[:12]}"
    admin = create_engine(_url_for("postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:  # pragma: no cover - environment problem, not a defect
        admin.dispose()
        pytest.skip(f"cannot create a scratch database: {exc}")

    url = _url_for(name)
    try:
        _run_alembic("upgrade", "head", url=url)
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def test_upgrade_then_downgrade_to_base(migrated_url: str) -> None:
    """The chain runs forward and back.

    Downgrade is rarely exercised and rots quietly. It matters on the day a deploy has to
    be rolled back, which is the worst possible day to discover a `downgrade` that
    forgot to drop an index.
    """
    _run_alembic("downgrade", "base", url=migrated_url)
    _run_alembic("upgrade", "head", url=migrated_url)


def test_models_match_migrations(migrated_url: str) -> None:
    """The schema the migrations build is the schema the models describe.

    This is the check that would have caught `score` being Integer in one place and
    Numeric in another. Without it, `create_all` and the migration chain are two
    independent definitions of the database and only one of them runs in production.
    """
    engine = create_engine(migrated_url.replace("+psycopg", ""))
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(
                conn,
                opts={
                    "include_name": lambda name, type_, parents: (
                        type_ != "table" or name not in SDK_OWNED_TABLES
                    )
                },
            )
            diff = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    assert not diff, (
        "The models and the migrations disagree:\n"
        + "\n".join(f"  {entry}" for entry in diff)
        + "\n\nGenerate a migration with `alembic revision --autogenerate`, or correct "
        "the model. Do not let `create_all` paper over it — production runs migrations."
    )
