"""Turning the agent's lifecycle into run events.

The plan called for driving this off `Runner.run_streamed().stream_events()`. `RunHooks` gives
the same information — which tool, with what arguments, returning what — plus per-response
token usage, and it works with a plain `Runner.run`. It also works with a fake model, so the
entire event path is testable without spending a token. Token-by-token deltas would buy the UI
nothing here: it renders a plan, a stage timeline and a tool trace, not a typewriter.

`write_todos` is special-cased: when it returns, the plan has changed, and the client wants
the new plan rather than the string "plan saved: 7 steps".
"""

from __future__ import annotations

import json
from typing import Any

from agents import Agent, RunContextWrapper, RunHooks
from agents.items import ModelResponse
from agents.run_context import AgentHookContext
from agents.tool import Tool
from sqlalchemy import select

from backend.api.events import EventPublisher
from backend.core.logging import get_logger
from backend.core.run_context import RunContext
from backend.workspace.models import AgentTodo

__all__ = ["EventHooks"]

log = get_logger(__name__)

#: Tool results are summaries, but a draft summary can still be long. The event stream is not
#: a place to store contract text; the workspace is.
_MAX_RESULT_CHARS = 600


class EventHooks(RunHooks[RunContext]):
    def __init__(self, publisher: EventPublisher) -> None:
        self._publisher = publisher

    async def on_tool_start(
        self, context: RunContextWrapper[RunContext], agent: Agent[RunContext], tool: Tool
    ) -> None:
        await self._publisher.emit("tool_call", tool=tool.name, agent=agent.name)

    async def on_tool_end(
        self,
        context: RunContextWrapper[RunContext],
        agent: Agent[RunContext],
        tool: Tool,
        result: object,
    ) -> None:
        text = str(result)
        await self._publisher.emit(
            "tool_result",
            tool=tool.name,
            agent=agent.name,
            output=text[:_MAX_RESULT_CHARS],
            truncated=len(text) > _MAX_RESULT_CHARS,
        )
        if tool.name == "write_todos":
            await self._emit_todos(context.context)

    async def on_llm_end(
        self,
        context: RunContextWrapper[RunContext],
        agent: Agent[RunContext],
        response: ModelResponse,
    ) -> None:
        """The orchestrator's own tokens. Sub-agent usage is accumulated by `run_subagent`."""
        run_context = context.context
        run_context.input_tokens += response.usage.input_tokens
        run_context.output_tokens += response.usage.output_tokens
        run_context.model_requests += response.usage.requests

    async def on_agent_start(
        self, context: AgentHookContext[RunContext], agent: Agent[RunContext]
    ) -> None:
        await self._publisher.stage(agent.name, "started")

    async def on_agent_end(
        self, context: AgentHookContext[RunContext], agent: Agent[RunContext], output: Any
    ) -> None:
        await self._publisher.stage(agent.name, "done")

    async def _emit_todos(self, context: RunContext) -> None:
        async with context.session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(AgentTodo)
                        .where(AgentTodo.contract_id == context.contract_id)
                        .order_by(AgentTodo.seq)
                    )
                )
                .scalars()
                .all()
            )
        await self._publisher.emit(
            "todo_update",
            todos=[{"task": r.task, "status": r.status} for r in rows],
        )


def summarise_arguments(raw: str, limit: int = 200) -> str:
    """Tool arguments, for the trace. Never the full contract text."""
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return raw[:limit]
    return json.dumps(parsed, sort_keys=True)[:limit]
