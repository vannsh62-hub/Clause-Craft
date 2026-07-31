"""Structured output + response shapes for the shared Contract Intelligence Engine.

One `ContractAnalysis` powers every intelligence widget (health score, suggestions,
explain, risk heatmap, negotiation mode, outline, analytics, relationships). Nothing
in this module calls a model — `ClauseIntelligenceModel`/`ContractIntelligenceModel`
are the agent's structured output; the deterministic pieces (word count, reading
time, variable counts, last-edited) are computed in `backend/phase_b/intelligence.py`
and merged onto the same per-clause record.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

RiskLevel = str  # "low" | "medium" | "high"


class ClauseRelationship(BaseModel):
    model_config = ConfigDict(frozen=True)

    clause_title: str
    kind: str  # "depends_on" | "referenced_by" | "extends"
    note: str = ""


class ClauseSuggestion(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    rationale: str = ""


class ClauseIntelligenceModel(BaseModel):
    """Per-clause slice of the single AI analysis call's structured output."""

    model_config = ConfigDict(frozen=True)

    clause_title: str
    risk: RiskLevel = "medium"
    risk_reason: str = ""
    summary: str = ""
    plain_english: str = ""
    business_purpose: str = ""
    negotiation_tips: str = ""
    common_alternatives: str = ""
    potential_problems: str = ""
    suggestions: tuple[ClauseSuggestion, ...] = ()
    depends_on: tuple[str, ...] = ()
    referenced_by: tuple[str, ...] = ()
    cross_references: tuple[str, ...] = ()


class ContractFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool  # True = "present" check-mark finding, False = warning
    text: str
    clause_title: str = ""  # empty when it's a missing-clause finding, not clause-linked


class ContractIntelligenceModel(BaseModel):
    """The single structured-output object the AI analysis call returns.

    Deliberately excludes anything deterministic (word counts, reading time, "last
    edited") — those never need a model and are merged in afterward, so re-running
    the (expensive) AI half is only necessary when clause *text* changes, not on
    every keystroke.
    """

    model_config = ConfigDict(frozen=True)

    findings: tuple[ContractFinding, ...] = ()
    missing_clauses: tuple[str, ...] = ()
    clauses: tuple[ClauseIntelligenceModel, ...] = ()


# ---------------------------------------------------------------------------
# API response shapes (what the frontend actually consumes)
# ---------------------------------------------------------------------------


class ClauseAnalytics(BaseModel):
    words: int
    reading_time_seconds: int
    readability: str
    variables: int
    cross_references: int
    ai_suggestions: int


class ClauseAnalysisOut(BaseModel):
    id: str
    title: str
    risk: RiskLevel
    risk_reason: str = ""
    summary: str = ""
    plain_english: str = ""
    business_purpose: str = ""
    negotiation_tips: str = ""
    common_alternatives: str = ""
    potential_problems: str = ""
    suggestions: list[ClauseSuggestion] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    referenced_by: list[str] = Field(default_factory=list)
    cross_references: list[str] = Field(default_factory=list)
    analytics: ClauseAnalytics
    variables_unresolved: list[str] = Field(default_factory=list)


class ContractAnalysisOut(BaseModel):
    health_score: int = Field(ge=0, le=100)
    findings: list[ContractFinding] = Field(default_factory=list)
    missing_clauses: list[str] = Field(default_factory=list)
    clauses: list[ClauseAnalysisOut] = Field(default_factory=list)
    cached: bool = False


class ContractAnalysisRequest(BaseModel):
    """Input to the engine. `document` is split into clause sections with the same
    '## '-heading rule as `frontend/lib/clauses.ts:parseClauseSections` (see
    `backend/phase_b/intelligence.py:split_clauses`) so clause boundaries are never
    derived two different ways."""

    document: str = Field(min_length=1, max_length=200_000)
    perspective: str = "neutral"  # "vendor" | "client" | "neutral" — negotiation mode