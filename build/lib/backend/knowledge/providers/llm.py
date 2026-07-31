"""The floor: what is known without consulting any source.

Always available, always lowest precedence. Its job is to guarantee that the provider set
is never empty — a run with no uploaded template, no clause library and no playbook still
has *something* to draft from, which is Mode 1.

It contributes only what the intent already established: jurisdiction, governing law,
country, language. That is deliberately thin. This provider is not where drafting knowledge
comes from — the drafting agent has the model's knowledge in its weights and does not need
it handed over as data. What it must not do is invent facts about *this* contract; a
provider that guessed a payment term would have that guess outrank nothing and be recorded
as knowledge.
"""

from __future__ import annotations

from backend.core.run_context import RunContext
from backend.knowledge.registry import register_provider
from backend.schemas.cko import ContractMetadata, Provenance
from backend.schemas.intent import IntentObject
from backend.schemas.provider import KnowledgeContribution

__all__ = ["LLMProvider"]


class LLMProvider:
    """Knowledge derivable from the request itself."""

    name = "llm"

    async def available(self, intent: IntentObject, ctx: RunContext) -> bool:
        """Always. This is the floor, and a run with no providers cannot draft."""
        return True

    async def contribute(self, intent: IntentObject, ctx: RunContext) -> KnowledgeContribution:
        return KnowledgeContribution(
            provider=self.name,
            provenance=Provenance(
                provider=self.name,
                locator="intent",
                confidence=intent.confidence,
            ),
            confidence=intent.confidence,
            metadata=ContractMetadata(
                country=intent.country,
                language=intent.language,
                jurisdiction=intent.jurisdiction,
                governing_law=intent.governing_law,
            ),
        )


register_provider(LLMProvider())
