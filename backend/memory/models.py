"""Memory tables.

`memory_facts` is **append-only**, like `contract_versions`. Superseding a fact writes a row and
points the old one at the new one; it never overwrites. What the system believed, and when, is
recoverable — which is what makes a conflict ("you said India in March and Singapore in August")
answerable rather than merely detectable.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base

SOURCES = ("user_confirmed", "carried_forward")
STABILITIES = ("stable", "volatile")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class MemoryFact(Base):
    __tablename__ = "memory_facts"
    __table_args__ = (
        CheckConstraint("source IN " + str(SOURCES), name="ck_memory_facts_source"),
        CheckConstraint("stability IN " + str(STABILITIES), name="ck_memory_facts_stability"),
        # `confidence` is 1.0 only when the user confirmed it. A carried-forward fact is a
        # question with a good prior, and the tool layer must not be able to launder it into an
        # answer by writing 1.0.
        CheckConstraint(
            "(source = 'user_confirmed' AND confidence = 1.0) OR "
            "(source = 'carried_forward' AND confidence < 1.0)",
            name="ck_memory_facts_confidence_matches_source",
        ),
        # One live fact per key. Superseded rows are unconstrained, so history accumulates.
        Index(
            "uq_memory_facts_live",
            "tenant_id",
            "user_id",
            "key",
            unique=True,
            postgresql_where=text("superseded_by IS NULL AND forgotten_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)

    source: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    stability: Mapped[str] = mapped_column(String(16), nullable=False)

    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    stale_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: Points at the row that replaced this one. Append-only: never updated in place.
    #:
    #: **Deferrable**, and it has to be. Superseding must mark the old row superseded *before*
    #: inserting the new one — otherwise both are momentarily live and the partial unique index
    #: below refuses, correctly. Deferring the FK lets the old row point at an id that does not
    #: exist yet; the constraint is checked at commit, by which time it does.
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("memory_facts.id", ondelete="SET NULL", deferrable=True, initially="DEFERRED"),
        nullable=True,
    )
    #: A tombstone. The row survives for audit; the fact stops being recalled.
    forgotten_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConversationSummary(Base):
    """Compressed *turns*, never compressed facts.

    A lossily-summarised fact is a fabricated fact, and this system does not fabricate values.
    Summaries exist to keep the context window affordable; `memory_facts` is where anything the
    contract depends on actually lives.
    """

    __tablename__ = "conversation_summaries"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False
    )

    summary: Mapped[str] = mapped_column(Text, nullable=False)
    turns_covered: Mapped[int] = mapped_column(nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


__all__ = ["ConversationSummary", "MemoryFact"]
