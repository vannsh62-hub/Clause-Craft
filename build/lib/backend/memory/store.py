"""The memory store.

Three rules live here rather than in the tools that call it, so a future tool cannot forget them:

1. **A key not in the allow-list is refused.** Deal particulars — a counterparty, an effective
   date, a fee — are never stored, whatever asks.
2. **A fact is never overwritten.** Superseding writes a row and points the old one at it.
3. **A differing value is a conflict, not an update.** The user said India in March and Singapore
   in August. The store cannot know whether that is a changed preference or a one-off deal, so it
   refuses to guess and hands the question back. Last-write-wins would silently switch a
   customer's jurisdiction.

Imports neither `agents` nor `openai`.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.principal import Principal
from backend.memory.models import MemoryFact
from backend.memory.stability import is_memorable, refusal_reason, stability_of, stale_after

__all__ = ["Conflict", "MemoryRefused", "MemoryHit", "MemoryStore", "Stored"]


class MemoryRefused(Exception):
    """The store declined to remember something. An unknown key, a deal particular, or a
    confidence that does not match its source."""


@dataclass(frozen=True)
class MemoryHit:
    key: str
    value: str
    source: str
    confidence: float
    confirmed_at: datetime
    stale: bool

    @property
    def usable_without_asking(self) -> bool:
        """Only a confirmed, fresh fact may fill a field without a question.

        A stale fact is a question with a good prior. A carried-forward fact is a guess with a
        good prior. Neither is an answer.
        """
        return self.source == "user_confirmed" and self.confidence >= 0.9 and not self.stale


@dataclass(frozen=True)
class Stored:
    key: str
    value: str
    superseded: str | None  # the previous value, if this replaced one


@dataclass(frozen=True)
class Conflict:
    """The user is telling us something different from what we hold. Ask them which it is."""

    key: str
    proposed: str
    existing: str
    existing_confirmed_at: datetime


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryStore:
    """Repository over `memory_facts`. Does not commit; the caller owns the transaction."""

    def __init__(self, session: AsyncSession, principal: Principal) -> None:
        self._session = session
        self._principal = principal

    def _scoped(self) -> Select[tuple[MemoryFact]]:
        return select(MemoryFact).where(
            MemoryFact.tenant_id == self._principal.tenant_id,
            MemoryFact.user_id == self._principal.user_id,
            MemoryFact.superseded_by.is_(None),
            MemoryFact.forgotten_at.is_(None),
        )

    async def _live(self, key: str) -> MemoryFact | None:
        stmt = self._scoped().where(MemoryFact.key == key)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    # ------------------------------------------------------------------ reading

    async def recall(self, keys: list[str]) -> list[MemoryHit]:
        """Facts we hold for these keys. Unknown keys yield nothing — silently, because asking
        for a key we do not remember is not an error, it is just a question the agent must ask."""
        wanted = [k for k in keys if is_memorable(k)]
        if not wanted:
            return []

        stmt = self._scoped().where(MemoryFact.key.in_(wanted))
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_hit(r) for r in rows]

    async def all_facts(self) -> list[MemoryHit]:
        rows = (await self._session.execute(self._scoped())).scalars().all()
        return sorted((self._to_hit(r) for r in rows), key=lambda h: h.key)

    def _to_hit(self, row: MemoryFact) -> MemoryHit:
        return MemoryHit(
            key=row.key,
            value=row.value,
            source=row.source,
            confidence=row.confidence,
            confirmed_at=row.confirmed_at,
            stale=row.stale_after <= _now(),
        )

    # ------------------------------------------------------------------ writing

    async def remember(
        self, key: str, value: str, *, source: str = "user_confirmed", confidence: float = 1.0
    ) -> Stored | Conflict:
        """Store a fact the user confirmed.

        Returns `Conflict` — and writes nothing — if a live fact holds a different value. The
        caller must resolve that with the user and call `supersede`.
        """
        self._check_writable(key, source, confidence)

        existing = await self._live(key)
        if existing is not None:
            if existing.value == value:
                return Stored(key=key, value=value, superseded=None)  # idempotent
            return Conflict(
                key=key,
                proposed=value,
                existing=existing.value,
                existing_confirmed_at=existing.confirmed_at,
            )

        await self._insert(key, value, source, confidence)
        return Stored(key=key, value=value, superseded=None)

    async def supersede(
        self, key: str, value: str, *, source: str = "user_confirmed", confidence: float = 1.0
    ) -> Stored:
        """Replace a fact, after the user has resolved the conflict. Append-only: the old row
        survives, pointing at the new one."""
        self._check_writable(key, source, confidence)

        existing = await self._live(key)
        new_id = uuid.uuid4()

        # Retire the old row FIRST. Only one fact per key may be live, and the partial unique
        # index enforces that — so inserting the replacement before retiring the original is a
        # unique violation, not a race we could have got away with. The FK is deferred, so the
        # old row may point at a row that does not exist until the flush below.
        if existing is not None:
            existing.superseded_by = new_id
            await self._session.flush()

        await self._insert(key, value, source, confidence, fact_id=new_id)
        return Stored(key=key, value=value, superseded=existing.value if existing else None)

    async def forget(self, key: str) -> bool:
        """Tombstone. The row survives for audit; the fact stops being recalled."""
        existing = await self._live(key)
        if existing is None:
            return False
        existing.forgotten_at = _now()
        await self._session.flush()
        return True

    async def forget_all(self) -> int:
        rows = (await self._session.execute(self._scoped())).scalars().all()
        now = _now()
        for row in rows:
            row.forgotten_at = now
        await self._session.flush()
        return len(rows)

    # ------------------------------------------------------------------ internals

    def _check_writable(self, key: str, source: str, confidence: float) -> None:
        if not is_memorable(key):
            raise MemoryRefused(refusal_reason(key))
        if source == "user_confirmed" and not math.isclose(confidence, 1.0):
            raise MemoryRefused("a user-confirmed fact has confidence 1.0")
        if source == "carried_forward" and confidence >= 1.0:
            raise MemoryRefused(
                "a carried-forward fact is a question with a good prior, not an answer; "
                "its confidence must be below 1.0"
            )
        if source not in ("user_confirmed", "carried_forward"):
            raise MemoryRefused(f"unknown source {source!r}")

    async def _insert(
        self,
        key: str,
        value: str,
        source: str,
        confidence: float,
        fact_id: uuid.UUID | None = None,
    ) -> MemoryFact:
        now = _now()
        fact = MemoryFact(
            id=fact_id or uuid.uuid4(),
            tenant_id=self._principal.tenant_id,
            user_id=self._principal.user_id,
            key=key,
            value=value,
            source=source,
            confidence=confidence,
            stability=stability_of(key),
            confirmed_at=now,
            stale_after=now + stale_after(key),
        )
        self._session.add(fact)
        await self._session.flush()
        return fact


def half_life(key: str) -> timedelta:
    return stale_after(key)
