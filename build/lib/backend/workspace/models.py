"""SQLAlchemy models for the contract workspace.

The workspace is a per-contract virtual filesystem backed by Postgres rather than disk.
That choice buys three things: paths are opaque database keys so there is no traversal
surface, the workspace survives a pod restart, and `clauses/` can be made read-only by a
column rather than by filesystem permissions.

Imported by alembic/env.py so these tables register on Base.metadata before autogenerate.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base

CONTRACT_STATUSES = ("planning", "awaiting_input", "drafting", "ready", "exported", "failed")
TODO_STATUSES = ("pending", "in_progress", "done", "cancelled")
RUN_STATUSES = ("running", "suspended", "complete", "failed")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Contract(Base):
    __tablename__ = "contracts"
    __table_args__ = (
        CheckConstraint("status IN " + str(CONTRACT_STATUSES), name="ck_contracts_status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    contract_type: Mapped[str | None] = mapped_column(String(64))
    jurisdiction: Mapped[str] = mapped_column(String(8), default="IN")
    status: Mapped[str] = mapped_column(String(32), default="planning")
    request: Mapped[str] = mapped_column(Text, default="")
    variables: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    files: Mapped[list[WorkspaceFile]] = relationship(
        back_populates="contract", cascade="all, delete-orphan"
    )


class WorkspaceFile(Base):
    """One file in a contract's workspace.

    `read_only` is enforced by `WorkspaceStore`, not by whichever tool happens to call it.
    Rendered clauses live under `clauses/` with `read_only=True`: the agent reads approved
    text and can never rewrite it.
    """

    __tablename__ = "workspace_files"
    __table_args__ = (
        UniqueConstraint("contract_id", "path", name="uq_workspace_files_contract_path"),
        Index("ix_workspace_files_contract_id", "contract_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    read_only: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Bumped on every write. Cheap optimistic-concurrency signal.
    version: Mapped[int] = mapped_column(Integer, default=1)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    contract: Mapped[Contract] = relationship(back_populates="files")


class AgentTodo(Base):
    """The agent's live plan. Written by the `write_todos` tool, streamed to the UI.

    This is the deep-agent planning property made durable and inspectable.
    """

    __tablename__ = "agent_todos"
    __table_args__ = (
        UniqueConstraint("contract_id", "seq", name="uq_agent_todos_contract_seq"),
        CheckConstraint("status IN " + str(TODO_STATUSES), name="ck_agent_todos_status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    task: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending")

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PendingQuestion(Base):
    """An `ask_user` call awaiting an answer.

    `call_id` is the whole point. When `ask_user` raises to suspend the run, the SDK has
    already appended the assistant's tool-call to the session with no matching tool output.
    On resume we must inject a tool-output item paired to this exact `call_id`, or the
    provider rejects the next completion.
    """

    __tablename__ = "pending_questions"
    __table_args__ = (UniqueConstraint("contract_id", "call_id", name="uq_pending_questions_call"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False
    )
    call_id: Mapped[str] = mapped_column(String(128), nullable=False)
    questions: Mapped[list[dict[str, object]]] = mapped_column(JSONB, default=list)
    answers: Mapped[dict[str, str] | None] = mapped_column(JSONB, nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ContractVersion(Base):
    """One drafting attempt. Append-only.

    This is the ledger. `RunContext.draft_attempts` is a cache rebuilt from `count(*)` here at
    the start of every run slice, because `ask_user` ends one slice and starts another — and a
    counter that lives only in memory would hand the agent a fresh attempt budget every time
    the user answered a question.

    It is also what makes `finalize_contract`'s best-passing-draft selection auditable after
    the fact: every attempt is retained with its score, not just the winner.
    """

    __tablename__ = "contract_versions"
    __table_args__ = (
        UniqueConstraint("contract_id", "attempt", name="uq_contract_versions_attempt"),
        # At most one finalized version per contract, enforced by Postgres rather than by
        # remembering to check. `export_docx` accepts only a finalized version id, so this is
        # the row that decides whether a document may exist.
        Index(
            "uq_contract_versions_one_final",
            "contract_id",
            unique=True,
            postgresql_where=text("finalized_at IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    markdown: Mapped[str] = mapped_column(Text, default="")

    #: Blob-storage key of the rendered DOCX for this attempt, when the drafting engine
    #: produced one. In template mode (Mode 2) this is the *edited source document* — the
    #: faithful bytes — and the export serves them directly rather than regenerating from
    #: `markdown`, which would discard the formatting the upload was meant to preserve. Null
    #: for legacy rows and the old orchestrator path, where the export regenerates.
    docx_storage_key: Mapped[str | None] = mapped_column(String(256), nullable=True)

    #: Null until the judge has scored this attempt.
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    clause_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)

    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)

    #: Set only by `finalize_contract`. Non-null means "a document may be produced from this".
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: True when the draft passed the gates but scored below the pass mark. Finalized anyway —
    #: the system never returns nothing — but never silently.
    needs_human_review: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JudgeReport(Base):
    __tablename__ = "judge_reports"

    id: Mapped[uuid.UUID] = _uuid_pk()
    contract_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contract_versions.id", ondelete="CASCADE"), nullable=False
    )
    judge_points: Mapped[int] = mapped_column(Integer, nullable=False)
    deterministic_points: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    findings: Mapped[list[dict[str, object]]] = mapped_column(JSONB, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Export(Base):
    """A generated document.

    Every export points at a `contract_version` that `finalize_contract` finalized. There is no
    row here that did not pass the gates, because there is no code path that could create one.
    """

    __tablename__ = "exports"

    id: Mapped[uuid.UUID] = _uuid_pk()
    contract_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contract_versions.id", ondelete="CASCADE"), nullable=False
    )
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(256), nullable=False)
    #: The document is byte-stable, so this identifies the content exactly.
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (CheckConstraint("status IN " + str(RUN_STATUSES), name="ck_runs_status"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), default="running")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RunEvent(Base):
    """Persisted, not merely streamed.

    A client that drops its SSE connection reconnects with `?seq=N` and replays from
    Postgres. Without this table a dropped connection silently loses the run.
    """

    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_run_events_run_seq"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


__all__ = [
    "AgentTodo",
    "Base",
    "Contract",
    "ContractVersion",
    "Export",
    "JudgeReport",
    "PendingQuestion",
    "Run",
    "RunEvent",
    "WorkspaceFile",
]


class ExtractedDocument(Base):
    __tablename__ = "extracted_documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contracts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(256), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExtractedClauseMatch(Base):
    __tablename__ = "extracted_clause_matches"

    id: Mapped[uuid.UUID] = _uuid_pk()
    extracted_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("extracted_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    clause_id: Mapped[str] = mapped_column(String(256), nullable=False)
    #: Cosine similarities and keyword counts both land here, so this is a float. It was
    #: previously `Integer` under a `Mapped[float]` annotation, which truncated every
    #: fractional score to 0 on write.
    score: Mapped[float] = mapped_column(Float, default=0.0)
    snippet: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


__all__.extend(["ExtractedDocument", "ExtractedClauseMatch"])
