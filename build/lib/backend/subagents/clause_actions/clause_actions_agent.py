"""Proposes structured document edits from a chat message, for the assistant panel.

Like `fill_details.fill_agent`, this never authors clause text. It can browse the approved
library (`list_clause_library`) but the only things it returns are *choices*: a clause id to
insert, an existing clause's title to replace/remove, or field values to fill — all applied
client-side through the same deterministic splice functions the manual editor uses
(`frontend/lib/clauses.ts`). Rendering the actual clause body remains
`invariants.render.render_clause`'s job alone.
"""

from __future__ import annotations

from backend.core.config import settings
from backend.core.prompts import load_prompt
from backend.core.run_context import RunContext
from backend.runtime.adapters.openai_agents import runtime
from backend.runtime.port import AgentRuntime
from backend.runtime.spec import AgentSpec
from backend.schemas.clause import ClauseActionSet

CLAUSE_ACTIONS_MAX_TURNS = 4

#: Single injection seam for tests, same convention as other spec-driven agents.
RUNTIME: AgentRuntime = runtime


def build_clause_actions_spec() -> AgentSpec[ClauseActionSet]:
    """The clause-mutation assistant, as data.

    Gets `list_clause_library` so it can name a real `clause_id` rather than guessing one;
    nothing else, since it never renders, writes, or drafts.
    """
    return AgentSpec(
        name="clause_actions_agent",
        prompt=load_prompt("clause_actions"),
        model=settings.retrieval_model,
        tools=("list_clause_library",),
        output_model=ClauseActionSet,
        max_turns=CLAUSE_ACTIONS_MAX_TURNS,
        temperature=0.2,
    )


async def propose_clause_actions(
    document: str,
    message: str,
    ctx: RunContext,
    history: list[tuple[str, str]] | None = None,
) -> ClauseActionSet:
    """Run the clause-mutation assistant for one chat turn against `document`.

    `history` is the prior (user message, assistant reply) pairs of this same
    conversation, oldest first — the caller's own record, since nothing is persisted
    server-side between calls. Without it every turn was effectively amnesiac: a
    follow-up like "add it" or "now do the next one" had nothing to resolve against.
    """
    transcript = ""
    if history:
        lines = []
        for prior_message, prior_reply in history:
            lines.append(f"User: {prior_message}")
            if prior_reply:
                lines.append(f"Assistant: {prior_reply}")
        transcript = "Prior conversation (oldest first):\n" + "\n".join(lines) + "\n\n"

    instruction = f"{transcript}Current document:\n{document}\n\nUser message: {message}"
    result = await RUNTIME.run(build_clause_actions_spec(), ctx, instruction)
    assert result.output is not None
    return result.output