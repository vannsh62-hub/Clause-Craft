"""The Contract Knowledge Object — everything Phase A understood.

This is the boundary of the system. Phase A produces exactly this and knows nothing about
what consumes it; Phase B receives exactly this and nothing else. Contract review, risk
analysis, clause recommendation and template generation are all meant to start from the
same object, which is the entire argument for separating understanding from drafting: if
understanding only existed as a local variable inside a drafting pipeline, every one of
those features would re-derive it.

Two decisions worth knowing about:

- **`schema_version` from day one.** This schema will be wrong, and it will be found out
  during Phase B when something needs a fact that was never captured. Growing it is
  expected and routine. Versioning from the start means an artifact written today is still
  readable after it grows.
- **`extra="ignore"`.** A CKO written by a newer version, read by an older one, drops
  fields it does not know rather than refusing the file. The alternative — refusing —
  turns a schema addition into an outage for in-flight runs.

Note that adding a *field here* is routine, while adding a *parameter to Phase B* is a
design review. The asymmetry is deliberate: the first keeps the boundary, the second
erodes it.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.intent import IntentObject, Party, ResolutionPlan
from backend.schemas.playbook import BusinessRule, PlaybookRequirement
from backend.schemas.template import FormattingManifest, Placeholder

__all__ = [
    "ClauseCandidate",
    "ClauseCandidateSet",
    "ConfidenceReport",
    "ContractKnowledgeObject",
    "ContractMetadata",
    "Definition",
    "KnowledgeConflict",
    "KnowledgeGraph",
    "MissingSection",
    "Provenance",
    "RiskLevel",
    "RiskSignal",
    "SemanticSection",
    "SemanticStructure",
    "SourceRef",
]

CKO_SCHEMA_VERSION = 1

RiskLevel = Literal["low", "medium", "high"]
ObligationKind = Literal["mutual", "unilateral", "none"]


class Provenance(BaseModel):
    """Where a piece of knowledge came from.

    Carried on conflicts so a resolution can be explained. "The playbook won" is only a
    useful answer if the loser is named too.
    """

    model_config = ConfigDict(frozen=True)

    provider: str
    locator: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class SourceRef(BaseModel):
    """A pointer back into a source document."""

    model_config = ConfigDict(frozen=True)

    provider: str
    block_id: str | None = None
    clause_id: str | None = None


class Definition(BaseModel):
    """A defined term and where it was defined."""

    model_config = ConfigDict(frozen=True)

    term: str
    meaning: str
    source_ref: SourceRef | None = None


class SemanticSection(BaseModel):
    """A section of the source document, by role rather than by position.

    The difference this makes: a parser reports "Heading 8, level 1, 4 paragraphs"; this
    reports "Termination — for cause and convenience, 30-day notice, survives §12". Only
    the second is something a transformation plan can reason about.

    A section the agent could not classify keeps `role=None` and lowers overall
    confidence, rather than being dropped. Silently discarding a section the system did
    not understand is how a contract loses a clause.
    """

    model_config = ConfigDict(frozen=True)

    block_id: str
    heading: str = ""
    role: str | None = None
    summary: str = ""
    order: int = Field(default=0, ge=0)
    defined_terms: tuple[str, ...] = ()
    cross_references: tuple[str, ...] = ()
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ClauseCandidate(BaseModel):
    """A clause, described rather than merely located.

    The highest-leverage object in the CKO. Clause recommendation, risk analysis, playbook
    validation and automatic clause-library construction all read this and nothing else,
    which is why `category` must come from the shared taxonomy in
    `skills/clause-taxonomy/` and never be free text: a free-text category makes candidates
    incomparable across contracts and destroys the reuse that justifies Phase A.
    Free-form nuance goes in `subcategory`.
    """

    model_config = ConfigDict(frozen=True)

    category: str
    subcategory: str | None = None
    purpose: str = ""
    applicability: tuple[str, ...] = ()
    risk: RiskLevel = "low"
    obligation: ObligationKind = "none"
    mandatory: bool = False
    negotiable: bool = True
    source_ref: SourceRef | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class SemanticStructure(BaseModel):
    """What the document's sections *are*, as opposed to where they sit.

    The output of the Contract Understanding Agent, and an artifact in its own right so
    that "what did the system think this document said?" is answerable without a re-run.

    `unclassified` names the blocks the agent could not place. Recording them lowers
    confidence and gives a reviewer somewhere to look; dropping them silently is how a
    contract loses a clause nobody notices is missing.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    sections: tuple[SemanticSection, ...] = ()
    definitions: tuple[Definition, ...] = ()
    unclassified: tuple[str, ...] = ()
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ClauseCandidateSet(BaseModel):
    """The Clause Classification Agent's output, wrapped so it can be an artifact.

    A bare list cannot carry a schema version, and this file will outlive the code that
    wrote it.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    candidates: tuple[ClauseCandidate, ...] = ()


class ContractMetadata(BaseModel):
    """The flat facts every downstream capability needs.

    Extracted separately, and cheaply, because contract review needs exactly this and
    nothing else from Phase A — making it wait on full semantic understanding would be
    wasteful for the commonest read.
    """

    model_config = ConfigDict(frozen=True)

    contract_name: str | None = None
    version: str | None = None
    effective_date: date | None = None
    duration: str | None = None
    country: str | None = None
    language: str = "en"
    currency: str | None = None
    notice_period_days: int | None = None
    payment_terms_days: int | None = None
    jurisdiction: str | None = None
    governing_law: str | None = None
    contract_value: Decimal | None = None


class KnowledgeGraph(BaseModel):
    """What a reference document taught us, with none of its words.

    A reference document is a source of knowledge, not of text. There is deliberately no
    field here that can hold a clause: the drafting agent cannot copy what it cannot see,
    and that is a structural guarantee rather than a prompt instruction. See spec 05 §7.
    """

    model_config = ConfigDict(frozen=True)

    document: str
    clause_categories: tuple[str, ...] = ()
    obligations: tuple[str, ...] = ()
    terminology: tuple[Definition, ...] = ()
    structure: tuple[str, ...] = ()
    negotiation_patterns: tuple[str, ...] = ()
    business_rules: tuple[BusinessRule, ...] = ()


class RiskSignal(BaseModel):
    """Something a reviewer should look at."""

    model_config = ConfigDict(frozen=True)

    category: str
    level: RiskLevel
    message: str
    source_ref: SourceRef | None = None


class MissingSection(BaseModel):
    """A section the contract type or playbook expects, which is not present."""

    model_config = ConfigDict(frozen=True)

    category: str
    reason: str
    required_by: str


class KnowledgeConflict(BaseModel):
    """Two sources disagreed, and the disagreement was recorded rather than resolved away.

    Both values and both provenances are kept. Silent precedence is how a playbook
    violation ships: the playbook wins, nobody is told the template said otherwise, and
    the discrepancy surfaces during negotiation instead of during review.
    """

    model_config = ConfigDict(frozen=True)

    field: str
    winning_value: str
    winning_provenance: Provenance
    losing_value: str
    losing_provenance: Provenance
    applied_precedence: str


class ConfidenceReport(BaseModel):
    """How much of this the system actually knows.

    Per-component as well as overall, because "0.6 confident" is not actionable while
    "0.9 on metadata, 0.4 on clause classification" tells a reviewer where to look.
    """

    model_config = ConfigDict(frozen=True)

    overall: float = Field(ge=0.0, le=1.0)
    components: tuple[tuple[str, float], ...] = ()


class ContractKnowledgeObject(BaseModel):
    """Everything Phase A understood. The only thing Phase B receives."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    schema_version: int = CKO_SCHEMA_VERSION

    # provenance
    contract_id: uuid.UUID
    tenant_id: uuid.UUID | None = None
    resolution: ResolutionPlan

    # intent
    intent: IntentObject

    # extracted facts
    metadata: ContractMetadata = ContractMetadata()
    parties: tuple[Party, ...] = ()
    definitions: tuple[Definition, ...] = ()

    # structure and meaning
    sections: tuple[SemanticSection, ...] = ()
    clause_candidates: tuple[ClauseCandidate, ...] = ()
    #: Present iff a template was resolved. Phase B refuses template mode without it.
    formatting: FormattingManifest | None = None
    placeholders: tuple[Placeholder, ...] = ()
    #: Storage key for the uploaded document, when there is one. Phase B needs the source
    #: bytes to edit in place (Mode 2), and the CKO is the only thing it receives — so the
    #: reference lives here rather than requiring Phase B to re-open a Phase A artifact.
    source_storage_key: str | None = None

    # rules
    playbook_rules: tuple[PlaybookRequirement, ...] = ()
    business_rules: tuple[BusinessRule, ...] = ()

    # knowledge without text
    reference_knowledge: tuple[KnowledgeGraph, ...] = ()

    # assessment
    risk_signals: tuple[RiskSignal, ...] = ()
    missing_sections: tuple[MissingSection, ...] = ()
    conflicts: tuple[KnowledgeConflict, ...] = ()
    confidence: ConfidenceReport = ConfidenceReport(overall=1.0)

    @property
    def blocking_requirements(self) -> tuple[PlaybookRequirement, ...]:
        return tuple(r for r in self.playbook_rules if r.blocking)
