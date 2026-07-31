"""Suggests values for clause placeholder fields the deterministic lookup couldn't fill.

Deliberately the simplest possible spec-driven agent: no tools, no workspace access, one
turn, structured output. It never authors clause text — `invariants/render.py` remains the
only thing that substitutes into a clause body. This agent only proposes *values* for a
modal the user reviews before anything is applied, which is why suggestions are advisory
(`FillSuggestionSet`) rather than an authoritative fill.
"""

from __future__ import annotations

from backend.core.config import settings
from backend.core.prompts import load_prompt
from backend.core.run_context import RunContext
from backend.runtime.adapters.openai_agents import runtime
from backend.runtime.port import AgentRuntime
from backend.runtime.spec import AgentSpec
from backend.schemas.clause import FillSuggestionSet

FILL_MAX_TURNS = 2

#: Single injection seam for tests, same convention as other spec-driven agents.
RUNTIME: AgentRuntime = runtime


def build_fill_spec() -> AgentSpec[FillSuggestionSet]:
    """The fill-suggestion agent, as data. Cheap model: this is a bounded, low-stakes
    suggestion task, not drafting or judging."""
    return AgentSpec(
        name="fill_details_agent",
        prompt=load_prompt("fill_details"),
        model=settings.retrieval_model,
        tools=(),
        output_model=FillSuggestionSet,
        max_turns=FILL_MAX_TURNS,
        temperature=0.2,
    )


async def suggest_fill_values(
    clause_text: str, fields: list[str], ctx: RunContext
) -> FillSuggestionSet:
    """Run the fill-suggestion agent for `fields` against `clause_text`."""
    instruction = (
        f"Clause text:\n{clause_text}\n\n"
        f"Fields to suggest values for: {', '.join(fields)}"
    )
    result = await RUNTIME.run(build_fill_spec(), ctx, instruction)
    assert result.output is not None
    return result.output
