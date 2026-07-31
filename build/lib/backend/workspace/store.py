"""The per-contract workspace: a virtual filesystem backed by Postgres.

The agent offloads work here rather than carrying it in context. A twelve-page draft across
three revision cycles costs the orchestrator a filename instead of forty thousand tokens.

Two invariants live in this module, and both are enforced *here* rather than in the tools
that call it — so they hold no matter which tool calls them, and a new tool cannot forget:

1. **`clauses/` is read-only.** Rendered clause text is written by the system and read by
   the agent. The agent can never rewrite approved text.
2. **Every operation is scoped to one `contract_id`.** There is no cross-contract read.

Paths are opaque database keys, never filesystem paths, so there is no traversal surface.
They are nonetheless canonicalised strictly — see `_canonical` for why that is about
lookalikes, not about `../`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.schemas.errors import WorkspaceError
from backend.workspace.models import WorkspaceFile

__all__ = ["READ_ONLY_PREFIX", "REFERENCE_PREFIX", "FileInfo", "WorkspaceStore"]

#: Where uploaded reference documents land. A workspace path convention, kept here — the
#: neutral layer both phases may import — so the reference provider that writes them and the
#: legal validator that reads them (to check for leaks) agree on one string rather than two.
REFERENCE_PREFIX = "references/"

#: Files under this prefix hold approved clause text. Written by the system, read by the agent.
READ_ONLY_PREFIX = "clauses/"

MAX_PATH_LEN = 512
_ALLOWED_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789._-/")


class FileInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    read_only: bool
    version: int
    size: int
    updated_at: datetime


def _canonical(path: str) -> str:
    """Validate a workspace path, or raise.

    The charset is deliberately narrow: lowercase, digits, `. _ - /`. Uppercase is rejected
    outright, and so are `..`, `.`, empty segments, and leading or trailing slashes.

    The reason is not directory traversal — these are database keys, and `clauses/../x` would
    simply be a row named `clauses/../x`. The reason is **lookalikes**. If `Clauses/nda.md`
    or `./clauses/nda.md` were storable, they would slip past the read-only prefix check while
    reading back to a later consumer as though they were approved clause text. One canonical
    spelling per file means the prefix check cannot be dodged by spelling.
    """
    if not path or len(path) > MAX_PATH_LEN:
        raise WorkspaceError(f"path must be 1..{MAX_PATH_LEN} characters, got {len(path)}")

    if illegal := sorted(set(path) - _ALLOWED_CHARS):
        raise WorkspaceError(
            f"illegal characters in path {path!r}: {illegal}. "
            "Use lowercase letters, digits, and '. _ - /' only."
        )

    if path.startswith("/") or path.endswith("/"):
        raise WorkspaceError(f"path must not start or end with '/': {path!r}")

    segments = path.split("/")
    if any(seg in ("", ".", "..") for seg in segments):
        raise WorkspaceError(f"path has an empty or relative segment: {path!r}")

    return path


def _is_read_only_path(path: str) -> bool:
    return path.startswith(READ_ONLY_PREFIX)


class WorkspaceStore:
    """Repository over `workspace_files`. Does not commit; the caller owns the transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _lock(self, contract_id: uuid.UUID) -> None:
        """Serialise writes for one contract for the rest of this transaction.

        Two writers can otherwise collide: the SDK issuing parallel tool calls in a single
        turn, or an orchestrator write racing a sub-agent write.
        """
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": str(contract_id)}
        )

    async def _get_row(self, contract_id: uuid.UUID, path: str) -> WorkspaceFile | None:
        stmt = select(WorkspaceFile).where(
            WorkspaceFile.contract_id == contract_id,  # scoping is never optional
            WorkspaceFile.path == path,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def _upsert(
        self, contract_id: uuid.UUID, path: str, content: str, *, read_only: bool
    ) -> FileInfo:
        await self._lock(contract_id)
        row = await self._get_row(contract_id, path)

        if row is None:
            row = WorkspaceFile(
                contract_id=contract_id, path=path, content=content, read_only=read_only, version=1
            )
            self._session.add(row)
        else:
            # Belt and braces. `write()` already refuses the `clauses/` prefix, but the
            # invariant should survive someone changing READ_ONLY_PREFIX: a read-only *row*
            # is never overwritten by a non-privileged caller.
            if row.read_only and not read_only:
                raise WorkspaceError(f"{path} is read-only and cannot be overwritten")
            row.content = content
            row.version += 1

        await self._session.flush()
        await self._session.refresh(row)
        return _to_info(row)

    # ---------------------------------------------------------------- agent-facing

    async def write(self, contract_id: uuid.UUID, path: str, content: str) -> FileInfo:
        """Create or overwrite an agent-owned file. Refuses `clauses/`."""
        p = _canonical(path)
        if _is_read_only_path(p):
            raise WorkspaceError(
                f"{p} is read-only: the clause library holds approved text and the agent "
                "may not rewrite it. Write your draft to draft_v<n>.md instead."
            )
        return await self._upsert(contract_id, p, content, read_only=False)

    async def read(self, contract_id: uuid.UUID, path: str) -> str:
        p = _canonical(path)
        row = await self._get_row(contract_id, p)
        if row is None:
            raise WorkspaceError(f"no such file: {p}. Call ls() to see the workspace.")
        return row.content

    async def ls(self, contract_id: uuid.UUID) -> tuple[FileInfo, ...]:
        stmt = (
            select(WorkspaceFile)
            .where(WorkspaceFile.contract_id == contract_id)
            .order_by(WorkspaceFile.path)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return tuple(_to_info(r) for r in rows)

    async def edit(self, contract_id: uuid.UUID, path: str, old: str, new: str) -> FileInfo:
        """Replace exactly one occurrence of `old`.

        Zero occurrences or several are both refused: a silent no-op and an unintended
        multi-replace are equally bad in a contract.
        """
        p = _canonical(path)
        if _is_read_only_path(p):
            raise WorkspaceError(f"{p} is read-only and cannot be edited.")
        if not old:
            raise WorkspaceError("edit() requires a non-empty `old` string")

        content = await self.read(contract_id, p)
        occurrences = content.count(old)
        if occurrences == 0:
            raise WorkspaceError(f"{p}: `old` text not found; nothing was changed")
        if occurrences > 1:
            raise WorkspaceError(
                f"{p}: `old` text appears {occurrences} times and is ambiguous. "
                "Include more surrounding context so it matches exactly once."
            )

        return await self._upsert(contract_id, p, content.replace(old, new), read_only=False)

    async def exists(self, contract_id: uuid.UUID, path: str) -> bool:
        return await self._get_row(contract_id, _canonical(path)) is not None

    # ---------------------------------------------------------------- system-facing

    async def put_clause(self, contract_id: uuid.UUID, path: str, content: str) -> FileInfo:
        """Place rendered, approved clause text into the read-only area.

        Privileged: reachable from `render_clause`, never from the agent's `write_file` tool.
        """
        p = _canonical(path)
        if not _is_read_only_path(p):
            raise WorkspaceError(f"clause files must live under {READ_ONLY_PREFIX}, got {p!r}")
        return await self._upsert(contract_id, p, content, read_only=True)


def _to_info(row: WorkspaceFile) -> FileInfo:
    return FileInfo(
        path=row.path,
        read_only=row.read_only,
        version=row.version,
        size=len(row.content),
        updated_at=row.updated_at,
    )
