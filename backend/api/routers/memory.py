"""Memory, as the user sees it.

A user who cannot inspect and delete what the system remembers about them does not have a memory
feature; they have a surveillance feature. These four routes are not an extra — they are the
condition on which storing anything is acceptable.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_session
from backend.core.principal import current_principal
from backend.memory.stability import MEMORABLE
from backend.memory.store import MemoryStore

router = APIRouter(prefix="/memory", tags=["memory"])


class FactOut(BaseModel):
    key: str
    value: str
    source: str
    confidence: float
    confirmed_at: datetime
    stale: bool
    usable_without_asking: bool


class KeyOut(BaseModel):
    key: str
    stability: str


@router.get("", response_model=list[FactOut])
async def list_facts(session: AsyncSession = Depends(get_session)) -> list[FactOut]:
    """Everything the system remembers about you."""
    hits = await MemoryStore(session, current_principal()).all_facts()
    return [
        FactOut(
            key=h.key,
            value=h.value,
            source=h.source,
            confidence=h.confidence,
            confirmed_at=h.confirmed_at,
            stale=h.stale,
            usable_without_asking=h.usable_without_asking,
        )
        for h in hits
    ]


@router.get("/keys", response_model=list[KeyOut])
async def list_memorable_keys() -> list[KeyOut]:
    """What the system is *capable* of remembering. Deliberately short, and deliberately public:
    it is the honest answer to "what do you keep about me"."""
    return [KeyOut(key=k, stability=v) for k, v in sorted(MEMORABLE.items())]


@router.delete("/{key}", status_code=204)
async def forget(key: str, session: AsyncSession = Depends(get_session)) -> None:
    forgotten = await MemoryStore(session, current_principal()).forget(key)
    await session.commit()
    if not forgotten:
        raise HTTPException(status_code=404, detail=f"nothing remembered for {key!r}")


@router.delete("", status_code=200)
async def forget_everything(session: AsyncSession = Depends(get_session)) -> dict[str, int]:
    """Erase the lot.

    Tombstones rather than deletes: the rows survive so that *what the system believed, and when*
    stays answerable, but nothing is recalled again. True erasure — destroying the values, not
    just hiding them — needs the crypto-shredding in `04-security-and-tenancy.md`, which is not
    built. Said plainly rather than implied.
    """
    count = await MemoryStore(session, current_principal()).forget_all()
    await session.commit()
    return {"forgotten": count}
