"""Rewrites a single clause instance per a free-text instruction, for the Edit popover's
✨ AI Assistant action.

Deliberately scoped like `fill_details.fill_agent`: no tools, no workspace access, one
turn, structured output. The agent is only ever shown the one clause's markdown — never
the surrounding document — so it has no way to touch anything else even if asked to.
Applying the suggestion (or not) remains the user's call in the modal's preview step.
"""

from __future__ import annotations

from backend.core.config import settings
from backend.core.prompts import load_prompt
from backend.core.run_context import RunContext
from backend.runtime.adapters.openai_agents import runtime
from backend.runtime.port import AgentRuntime
from backend.runtime.spec import AgentSpec
from backend.schemas.clause import ClauseEditSuggestion

CLAUSE_EDIT_MAX_TURNS = 2

#: Single injection seam for tests, same convention as other spec-driven agents.
RUNTIME: AgentRuntime = runtime


def build_clause_edit_spec() -> AgentSpec[ClauseEditSuggestion]:
    """The single-clause AI-edit agent, as data. Cheap model: this is a bounded, single-
    clause rewrite, not drafting or judging."""
    return AgentSpec(
        name="clause_edit_agent",
        prompt=load_prompt("clause_edit"),
        model=settings.retrieval_model,
        tools=(),
        output_model=ClauseEditSuggestion,
        max_turns=CLAUSE_EDIT_MAX_TURNS,
        temperature=0.2,
    )


async def suggest_clause_edit(
    clause_markdown: str, instruction: str, ctx: RunContext
) -> ClauseEditSuggestion:
    """Run the AI-edit agent against one clause's current markdown."""
    prompt = (
        f"Current clause:\n{clause_markdown}\n\n"
        f"Instruction: {instruction}"
    )
    result = await RUNTIME.run(build_clause_edit_spec(), ctx, prompt)
    assert result.output is not None
    return result.output
