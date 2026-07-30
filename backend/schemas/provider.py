"""What a knowledge provider contributes.

Every knowledge source — uploaded template, clause library, reference document, playbook,
the model's own knowledge — returns one of these. The pipeline never branches on which
kind of source it is talking to, which is what makes a fifth source a new file rather than
a pipeline change.

Providers are **independent**: none may read another's output. All cross-source
reconciliation happens once, in the aggregator, where precedence is explicit and conflicts
are recorded. A provider that peeked at another's contribution would make precedence
depend on execution order.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.cko import (
    ClauseCandidate,
    ContractMetadata,
    Definition,
    KnowledgeGraph,
    Provenance,
    SemanticSection,
)
from backend.schemas.playbook import BusinessRule, PlaybookRequirement
from backend.schemas.template import FormattingManifest

__all__ = ["KnowledgeContribution"]


class KnowledgeContribution(BaseModel):
    """One provider's answer, in the shared vocabulary.

    Every field is optional. A playbook contributes requirements and nothing else; a
    template contributes sections and formatting and no requirements. The uniform shape is
    the point — the aggregator merges contributions without knowing what produced them.

    `confidence` and `provenance` travel with the contribution rather than being attached
    later, because by the time two values conflict the aggregator no longer has the
    context to judge which source was surer of itself.
    """

    model_config = ConfigDict(frozen=True)

    provider: str
    provenance: Provenance
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    sections: tuple[SemanticSection, ...] = ()
    clause_candidates: tuple[ClauseCandidate, ...] = ()
    definitions: tuple[Definition, ...] = ()
    metadata: ContractMetadata | None = None
    formatting: FormattingManifest | None = None
    #: Storage key for an uploaded document this provider read, when it edits in place.
    source_storage_key: str | None = None
    requirements: tuple[PlaybookRequirement, ...] = ()
    business_rules: tuple[BusinessRule, ...] = ()
    reference_knowledge: tuple[KnowledgeGraph, ...] = ()

    @property
    def is_empty(self) -> bool:
        """True when a provider ran but found nothing.

        Distinct from a provider that did not run at all: the first is a fact about the
        source, the second is a fact about the resolution. Both end up in the trace.
        """
        return not any(
            (
                self.sections,
                self.clause_candidates,
                self.definitions,
                self.metadata,
                self.formatting,
                self.requirements,
                self.business_rules,
                self.reference_knowledge,
            )
        )
