"""The drafting sub-agent, exposed to the orchestrator as a tool.

Stateless across attempts. Revision feedback arrives as a **file path**, not as conversation
history — attempt 3 is not attempt 2 plus an argument, it is a fresh drafting job with a list
of defects.

The tool takes **no arguments**. The attempt number, the draft path, and the feedback file are
all derived server-side from the append-only ledger. A model that cannot name the file it is
writing cannot overwrite attempt 2 while claiming to be attempt 3, and cannot talk its way
into a fourth attempt.
"""

from __future__ import annotations

from typing import Any

from agents import RunContextWrapper, function_tool

from backend.core.config import settings
from backend.core.prompts import load_prompt
from backend.core.run_context import RunContext
from backend.runtime.adapters.openai_agents import runtime
from backend.runtime.port import AgentRuntime
from backend.runtime.spec import AgentSpec
from backend.schemas.errors import AttemptsExhausted, format_tool_error
from backend.workspace.ledger import count_attempts
from backend.workspace.models import ContractVersion
from backend.workspace.store import WorkspaceStore

DRAFTING_MAX_TURNS = 12

DRAFT_PATH = "draft_v{n}.md"
FEEDBACK_PATH = "findings_v{n}.json"


#: Replaced by tests with a runtime carrying a fake model. See `judge_agent.RUNTIME`.
RUNTIME: AgentRuntime = runtime


def build_drafting_spec() -> AgentSpec[Any]:
    """The drafting sub-agent, as data.

    It reads clauses and writes its draft. It plans nothing — a drafting agent that can
    call `write_todos` is an orchestrator by another name. No `output_model`: the draft
    is a file, and what comes back is a summary for the orchestrator's benefit.
    """
    return AgentSpec(
        name="drafting_agent",
        prompt=load_prompt("drafting"),
        model=settings.drafting_model,
        tools=("read_file", "ls_files", "write_file", "calculate_dates"),
        max_turns=DRAFTING_MAX_TURNS,
        temperature=0.2,
    )


def _instruction(draft_path: str, previous: str | None, feedback_path: str | None) -> str:
    lines = [
        "Assemble the contract from the approved clauses in `clauses/`.",
        f"Write the finished draft to `{draft_path}`.",
    ]
    if previous and feedback_path:
        lines += [
            f"This is a revision of `{previous}`.",
            f"Read `{feedback_path}` first: it lists exactly what to fix.",
            "Fix those defects and nothing else. Keep every clause verbatim.",
        ]
    return "\n".join(lines)


@function_tool(failure_error_function=format_tool_error)
async def run_drafting_agent(wrapper: RunContextWrapper[RunContext]) -> str:
    """Delegate drafting to the drafting sub-agent. Call `render_clauses` first.

    Each call is one attempt. The attempt number and file name are decided here, not by you.
    On a second or third attempt the sub-agent is automatically given the judge's findings from
    the previous attempt. There are only three attempts.

    The sub-agent never sees this conversation.
    """
    context = wrapper.context

    async with context.session_factory() as session:
        attempt = await count_attempts(session, context.contract_id) + 1

    if attempt > settings.max_draft_attempts:
        raise AttemptsExhausted(
            f"all {settings.max_draft_attempts} drafting attempts are used. "
            "Finalize the best draft you have, or ask the user how to proceed."
        )

    draft_path = DRAFT_PATH.format(n=attempt)
    previous = DRAFT_PATH.format(n=attempt - 1) if attempt > 1 else None
    feedback_path = FEEDBACK_PATH.format(n=attempt - 1) if attempt > 1 else None

    async with context.session_factory() as session:
        store = WorkspaceStore(session)
        if feedback_path and not await store.exists(context.contract_id, feedback_path):
            feedback_path = previous = None

    before_in, before_out = context.input_tokens, context.output_tokens
    result = await RUNTIME.run(
        build_drafting_spec(),
        context,
        _instruction(draft_path, previous, feedback_path),
    )

    async with context.session_factory() as session:
        markdown = await WorkspaceStore(session).read(context.contract_id, draft_path)
        session.add(
            ContractVersion(
                contract_id=context.contract_id,
                attempt=attempt,
                path=draft_path,
                markdown=markdown,
                input_tokens=context.input_tokens - before_in,
                output_tokens=context.output_tokens - before_out,
            )
        )
        await session.commit()

    context.draft_attempts = attempt
    remaining = settings.max_draft_attempts - attempt
    summary = result.text.strip()[:400]
    return (
        f"attempt {attempt} of {settings.max_draft_attempts} written to {draft_path} "
        f"({remaining} attempt(s) left). {summary}"
    )
