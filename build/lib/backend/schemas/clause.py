from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ClauseVariable(BaseModel):
    """One clause variable, as a name/value pair.

    Not a `dict[str, str]`: OpenAI strict function schemas forbid open-ended objects, so a
    free-form mapping cannot be a tool parameter. An explicit pair list is also easier for a
    model to fill in correctly.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    value: str


class Clause(BaseModel):
    """An approved clause as it exists in the library.

    `body` is a Jinja2 template. It is rendered by `invariants.render.render_clause`
    and never authored by a model.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    version: int = Field(ge=1)
    title: str
    contract_types: tuple[str, ...]
    jurisdictions: tuple[str, ...]
    required: bool
    order: int = Field(ge=0)
    variables: tuple[str, ...]
    body: str
    source_path: str
    #: Optional risk posture, for the library view and the ranker. Defaults to "medium" so
    #: existing clause files that predate the field load unchanged.
    risk: str = "medium"


class FillSuggestionSet(BaseModel):
    """Structured output of the fill-details suggestion agent.

    A pair list rather than `dict[str, str]` for the same reason as `ClauseVariable`:
    OpenAI strict function schemas forbid open-ended objects.
    """

    model_config = ConfigDict(frozen=True)

    suggestions: tuple[ClauseVariable, ...] = ()
    #: Field names the model deliberately declined to guess at, per the prompt's rule
    #: against inventing party-specific facts.
    unresolved: tuple[str, ...] = ()


class ClauseEditSuggestion(BaseModel):
    """Structured output of the single-clause AI-edit agent.

    Scoped to exactly one clause instance: `updated_clause` is the full replacement
    section markdown (heading + body) for that clause only. The agent never sees, and
    therefore cannot touch, any other part of the document — the caller sends it only
    this one clause's markdown plus the user's free-text instruction.
    """

    model_config = ConfigDict(frozen=True)

    updated_clause: str = Field(
        description="the full replacement clause markdown, including its '## ' heading"
    )
    summary: str = Field(default="", description="one sentence describing what changed")


class ClauseAction(BaseModel):
    """One proposed edit to the document, for the user to review and apply.

    Flat rather than a tagged union of per-action models, for the same strict-schema
    reason as `ClauseVariable`: a model's structured output is easiest to constrain and
    fill in correctly as one shape. Which fields apply depends on `action`:

    - ``insert``: `clause_id` (library clause to render) and, optionally,
      `after_clause_title` (the existing clause to insert after; omitted means the end
      of the document).
    - ``replace``: `clause_title` (the existing clause to swap out) and `clause_id` (the
      library clause to render in its place).
    - ``remove``: `clause_title` (the existing clause to act on).
    - ``fill``: `clause_title` and `fields` (values to apply to that clause's
      placeholders).

    This model never carries clause *text*. `insert` names a library clause id, which the
    frontend renders through the existing `/clauses/{id}/render` endpoint; `fill` values
    are the same shape the Fill-details modal already applies. The model proposes
    *targets and choices*, never prose — the same boundary `render_clause` enforces
    elsewhere.
    """

    model_config = ConfigDict(frozen=True)

    action: str = Field(description="one of: insert, replace, remove, fill")
    clause_title: str = Field(
        default="", description="the existing clause this action targets (replace/remove/fill)"
    )
    clause_id: str = Field(
        default="", description="a library clause id to insert (insert only)"
    )
    after_clause_title: str = Field(
        default="", description="insert after this existing clause; empty means at the end"
    )
    fields: tuple[ClauseVariable, ...] = Field(
        default=(), description="values to fill into clause_title's placeholders (fill only)"
    )
    reason: str = Field(default="", description="one sentence explaining this proposal")


class ClauseActionSet(BaseModel):
    """Structured output of the clause-mutation assistant: what it proposes, plus what
    it says back to the user. The reply is always shown; actions may be empty if the
    request didn't call for any document change."""

    model_config = ConfigDict(frozen=True)

    reply: str
    actions: tuple[ClauseAction, ...] = ()


class RenderedClause(BaseModel):
    """A clause with its variables substituted.

    `text` is produced by the template engine. No model contributes to it. `source_sha`
    is the sha256 of the clause body that produced this text, so any draft containing
    this clause can be traced to `clause_id@version` and byte-verified against the library.
    """

    model_config = ConfigDict(frozen=True)

    clause_id: str
    version: int
    title: str
    order: int
    text: str
    source_sha: str

    @property
    def provenance(self) -> str:
        return f"{self.clause_id}@{self.version}"
