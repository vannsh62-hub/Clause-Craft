from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DraftRequest(BaseModel):
    request: str = Field(min_length=1, max_length=4000)
    clause_ids: list[str] | None = None


class RunAccepted(BaseModel):
    contract_id: uuid.UUID
    run_id: uuid.UUID
    events: str = Field(description="SSE endpoint for this run")


class ReplicateAccepted(BaseModel):
    """Several runs started from one uploaded document, one entry per copy."""

    contracts: list[RunAccepted]


class AnswersRequest(BaseModel):
    answers: dict[str, str] = Field(min_length=1)


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    attempt: int
    score: int | None
    passed: bool | None
    needs_human_review: bool | None
    finalized_at: datetime | None
    clause_ids: list[str]


class ContractOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    contract_type: str | None
    jurisdiction: str
    status: str
    request: str
    created_at: datetime
    #: Every deal-term/party value resolved for this contract so far — the Contract Variable
    #: Memory. Exposed (already alias-resolved, same as `/render` and `/clauses/analyse`) so the
    #: frontend can prefill Fill-details/Insert/AI-edit without a round trip for every clause.
    variables: dict[str, str] = Field(default_factory=dict)


class FileOut(BaseModel):
    path: str
    read_only: bool
    version: int
    size: int
    updated_at: datetime


class TodoOut(BaseModel):
    task: str
    status: str


class ArtifactOut(BaseModel):
    """One named pipeline artifact and whether it has been produced yet."""

    name: str
    path: str
    present: bool


class ContractSummaryOut(BaseModel):
    """A past contract, for the history and projects lists."""

    id: uuid.UUID
    request: str
    contract_type: str | None
    status: str
    created_at: datetime
    #: True once a draft has been finalized — what makes a contract a "project" rather than
    #: an in-progress conversation.
    finalized: bool


class ClauseOut(BaseModel):
    id: str
    version: int
    title: str
    contract_type: str
    required: bool
    order: int
    variables: list[str]
    #: Library-view fields. `category` is the clause's title; `country` is its jurisdiction
    #: rendered for display; `status` is always "Approved" — a clause in the library has been
    #: reviewed, which is what the field means.
    category: str = ""
    country: str = "Global"
    risk: str = "medium"
    status: str = "Approved"
    body: str = ""


class ClauseWrite(BaseModel):
    """Fields for creating or editing a clause. Variables are derived from the body, so the
    caller does not supply them."""

    id: str
    title: str
    contract_type: str
    country: str = "IN"
    required: bool = False
    order: int = 50
    risk: str = "medium"
    body: str


class ClauseAnalyseRequest(BaseModel):
    """Placeholder field names the frontend's deterministic parser found in a clause
    instance (e.g. from `{{ duration_years }}` or an `[INSERT ...]` bracket)."""

    fields: list[str] = Field(min_length=1, max_length=50)


class ClauseAnalyseResponse(BaseModel):
    """What this contract already knows, split from what it genuinely doesn't.

    `known` only contains entries for names in the request's `fields` — this is a
    targeted lookup for one clause instance, not a dump of every contract variable.
    """

    known: dict[str, str]
    missing: list[str]


class ClauseFillRequest(BaseModel):
    """Ask the assistant to suggest values for the fields `analyse` could not resolve.

    `clause_text` gives the model the surrounding clause for context (party roles,
    defined terms already used nearby); it is read-only context, never rewritten.
    """

    clause_text: str = Field(min_length=1, max_length=20_000)
    fields: list[str] = Field(min_length=1, max_length=50)


class ClauseFillResponse(BaseModel):
    """Suggested values for the requested fields. Suggestions, not facts — the frontend
    still shows them in editable inputs rather than applying them silently."""

    suggestions: dict[str, str]
    unresolved: list[str]


class ContractVariablesUpdate(BaseModel):
    """New values the user just confirmed while filling in a clause — the write side of the
    Contract Variable Memory. Keys are whatever the frontend's placeholder parser detected
    (`client_name`, `Client Name`, ...); `resolve_variables`'s alias table normalizes them onto
    the same canonical keys `/render` and `/clauses/analyse` already read from, so a value saved
    here is picked up by every other feature without a separate merge step."""

    values: dict[str, str] = Field(min_length=1, max_length=50)


class ContractVariablesResponse(BaseModel):
    """The contract's full resolved variable set after the merge — the frontend replaces its
    local cache with this rather than patching it, so it can never drift from what's stored."""

    variables: dict[str, str]


class ClauseEditSuggestRequest(BaseModel):
    """The Edit popover's ✨ AI Assistant action: rewrite one clause per a free-text
    instruction. `clause_markdown` is that clause's current section only (heading +
    body) — the agent never sees the rest of the document."""

    clause_markdown: str = Field(min_length=1, max_length=20_000)
    instruction: str = Field(min_length=1, max_length=2000)


class ClauseEditSuggestResponse(BaseModel):
    """Suggested rewrite for the popover's side-by-side preview. Nothing is applied to
    the document by this call — the user still approves or rejects it."""

    updated_clause: str
    summary: str


class ClauseActionOut(BaseModel):
    """One proposed edit, as sent to the client. Mirrors `backend.schemas.clause.ClauseAction`
    but as a plain response model (fields as a dict, easier for the frontend to consume)."""

    action: str
    clause_title: str = ""
    clause_id: str = ""
    after_clause_title: str = ""
    fields: dict[str, str] = Field(default_factory=dict)
    reason: str = ""


class ClauseActionsHistoryTurn(BaseModel):
    """One earlier turn of the same clause-assistant conversation, sent back on the next
    request so the model has context beyond the current message alone (e.g. "add it" or
    "do the next one" referring to something proposed a turn or two earlier)."""

    message: str = Field(min_length=1, max_length=2000)
    reply: str = Field(default="", max_length=4000)


class ClauseActionsRequest(BaseModel):
    """One turn of the assistant panel: the user's message plus the document as it
    currently stands, so proposals can reference real clause titles. `history` carries
    the prior turns of this same conversation (oldest first) — the client's own record,
    since nothing is persisted server-side between calls."""

    message: str = Field(min_length=1, max_length=2000)
    document: str = Field(min_length=1, max_length=200_000)
    history: list[ClauseActionsHistoryTurn] = Field(default_factory=list, max_length=20)


class ClauseActionsResponse(BaseModel):
    reply: str
    actions: list[ClauseActionOut]


class DocumentSaveRequest(BaseModel):
    """The document as it stands in the editor — manual edits and/or applied assistant
    clause actions (insert/replace/remove/fill) — to be persisted past the page reload
    that previously discarded them."""

    markdown: str = Field(min_length=1, max_length=200_000)


class DocumentSaveResponse(BaseModel):
    path: str
    version_id: uuid.UUID
    updated_at: datetime


class ExportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    format: str
    sha256: str
    size_bytes: int
    created_at: datetime