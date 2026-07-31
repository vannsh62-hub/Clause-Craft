"""The Contract Intelligence Engine's single model call.

One call produces health findings, risk, suggestions, explanations, and relationships for
every clause at once (`ContractIntelligenceModel`). `backend/phase_b/intelligence.py` is the
caller: it merges this with the deterministic analytics (word count, reading time, unresolved
variables) and caches the merged result by content hash, so this agent is never invoked twice
for the same document + perspective.
"""

from __future__ import annotations

from backend.core.config import settings
from backend.core.prompts import load_prompt
from backend.core.run_context import RunContext
from backend.runtime.adapters.openai_agents import runtime
from backend.runtime.port import AgentRuntime
from backend.runtime.spec import AgentSpec
from backend.schemas.intelligence import ContractIntelligenceModel

INTELLIGENCE_MAX_TURNS = 2

#: Single injection seam for tests, same convention as other spec-driven agents.
RUNTIME: AgentRuntime = runtime


def build_intelligence_spec() -> AgentSpec[ContractIntelligenceModel]:
    """One shared analysis agent. Retrieval-tier model: this is grounded read/summarize
    work over supplied text, not drafting or judging."""
    return AgentSpec(
        name="contract_intelligence_agent",
        prompt=load_prompt("contract_intelligence"),
        model=settings.retrieval_model,
        tools=(),
        output_model=ContractIntelligenceModel,
        max_turns=INTELLIGENCE_MAX_TURNS,
        temperature=0.2,
    )


async def analyze_contract(
    document: str, clause_titles: list[str], perspective: str, ctx: RunContext
) -> ContractIntelligenceModel:
    """Run the analysis agent once for the whole `document`."""
    instruction = (
        f"Negotiation perspective: {perspective}\n"
        f"Existing clause titles: {', '.join(clause_titles) or '(none)'}\n\n"
        f"Contract markdown:\n{document}"
    )
    result = await RUNTIME.run(build_intelligence_spec(), ctx, instruction)
    assert result.output is not None
    return result.output