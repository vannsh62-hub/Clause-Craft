"""Running an `AgentSpec` on the OpenAI Agents SDK.

This is the only module in the agent path that imports `agents`. Everything specific to
the SDK is contained here: building an `Agent`, the `session=None` isolation argument,
reading usage off `raw_responses`, and translating the SDK's exception vocabulary back to
the project's own.

The behaviour is deliberately identical to `backend/subagents/common.py::run_subagent`,
which this replaces for spec-driven agents. Two properties are carried over verbatim
because both were learned from real failures:

- **Budget is checked before starting, not after.** Never begin a sub-agent that cannot be
  afforded to finish.
- **Sub-agent usage is charged to the parent.** Without it, delegation is a way to spend
  an unbounded budget.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

from agents import Agent, FunctionTool, ModelSettings, RunConfig, Runner
from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError
from agents.models.interface import Model

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.core.run_context import RunContext
from backend.runtime.parse import coerce_output
from backend.runtime.spec import AgentResult, AgentSpec, HistoryHandle, Out
from backend.schemas.errors import ContractToolError, CostCeilingExceeded
from backend.tools.registry import ORCHESTRATOR_TOOLS

__all__ = ["OpenAIAgentsRuntime", "runtime"]

log = get_logger(__name__)

#: Tracing off: spans carry tool arguments and model output — party names, contract text.
RUN_CONFIG = RunConfig(tracing_disabled=True, trace_include_sensitive_data=False)


def _tool_index() -> Mapping[str, FunctionTool]:
    """Resolve tool names to SDK tools.

    The agent-invoking tools (`run_drafting_agent`, `run_judge_agent`) live in modules that
    import *this* one, so they are imported inside the function rather than at module
    scope. By the time anything calls this, those modules are loaded; at import time they
    would be a cycle.

    `ask_user` keeps `failure_error_function=None` because it is registered that way at its
    definition — prebuilt tools are passed through untouched, so the one deliberate
    exception to the error-handler rule survives the port.
    """
    from backend.subagents.drafting.drafting_agent import run_drafting_agent
    from backend.subagents.judge.judge_agent import run_judge_agent
    from backend.tools.export_tool import export_docx
    from backend.tools.finalize_tool import finalize_contract
    from backend.tools.memory_tool import (
        forget_memory,
        recall_memory,
        remember_fact,
        resolve_memory_conflict,
    )
    from backend.tools.user_tool import ask_user

    tools: tuple[FunctionTool, ...] = (
        *ORCHESTRATOR_TOOLS,
        recall_memory,
        remember_fact,
        resolve_memory_conflict,
        forget_memory,
        run_drafting_agent,
        run_judge_agent,
        finalize_contract,
        export_docx,
        ask_user,
    )
    return {tool.name: tool for tool in tools}


class OpenAIAgentsRuntime:
    """`AgentRuntime` over `agents.Runner`."""

    name = "openai_agents"

    def __init__(self, model_override: str | Model | None = None) -> None:
        #: Tests inject a `FakeModel` here so the whole port is exercised without a network.
        self._model_override = model_override

    def build_agent(self, spec: AgentSpec[Out]) -> Agent[RunContext]:
        """Materialise a spec as an SDK agent.

        Public because the supervisor's slice loop in
        `backend/subagents/orchestrator/deep_agent.py` still drives `Runner.run` itself —
        it owns conversation history, event hooks, and the suspend/resume outcome mapping,
        none of which belong in a single-shot `run()`. Its *definition* is a spec even
        though its *execution* is not yet behind the port.
        """
        index = _tool_index()
        missing = [name for name in spec.tools if name not in index]
        if missing:
            raise ContractToolError(f"{spec.name} requests unknown tools: {sorted(missing)}")
        return Agent[RunContext](
            name=spec.name,
            instructions=spec.prompt,
            model=self._model_override or spec.model,
            model_settings=ModelSettings(
                temperature=spec.temperature,
                parallel_tool_calls=spec.parallel_tool_calls,
            ),
            tools=[index[name] for name in spec.tools],
            output_type=spec.output_model,
        )

    def _check_budget(self, ctx: RunContext) -> None:
        if ctx.total_tokens >= settings.max_total_tokens:
            raise CostCeilingExceeded(ctx.total_tokens, settings.max_total_tokens)

    async def run(
        self,
        spec: AgentSpec[Out],
        ctx: RunContext,
        instruction: str,
        *,
        history: HistoryHandle | None = None,
    ) -> AgentResult[Out]:
        self._check_budget(ctx)

        if history is not None:  # pragma: no cover - the supervisor owns its own session
            raise ContractToolError(
                "history is not supported by this adapter yet; the supervisor manages its "
                "own session in backend/subagents/orchestrator/deep_agent.py"
            )

        try:
            result = await Runner.run(
                self.build_agent(spec),
                input=instruction,
                context=ctx,
                max_turns=spec.max_turns,
                session=None,  # the isolation, in one argument
                run_config=RUN_CONFIG,
            )
        except MaxTurnsExceeded as exc:
            raise ContractToolError(
                f"{spec.name} exceeded {spec.max_turns} turns without finishing. "
                "Give it a narrower task, or take a different approach."
            ) from exc
        except ModelBehaviorError as exc:
            raise ContractToolError(f"{spec.name} returned malformed output; retry once.") from exc

        input_tokens = sum(r.usage.input_tokens for r in result.raw_responses)
        output_tokens = sum(r.usage.output_tokens for r in result.raw_responses)
        requests = sum(r.usage.requests for r in result.raw_responses)

        ctx.input_tokens += input_tokens
        ctx.output_tokens += output_tokens
        ctx.model_requests += requests

        log.info(
            "agent=%s turns=%d in_tokens=%d out_tokens=%d",
            spec.name,
            len(result.raw_responses),
            input_tokens,
            output_tokens,
        )

        output = coerce_output(spec, result.final_output) if spec.output_model else None
        return AgentResult(
            text=str(result.final_output),
            output=output,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model_requests=requests,
        )

    async def run_many(
        self,
        jobs: Sequence[tuple[AgentSpec[Any], str]],
        ctx: RunContext,
    ) -> list[AgentResult[Any]]:
        """Concurrent `Runner.run`s over one shared context.

        The context is mutated by each run to record usage. That is safe under asyncio —
        `+=` on an int attribute does not await — but it does mean the budget is only
        re-checked between batches, so a fan-out can overshoot the ceiling by at most one
        batch. Keep batches small.
        """
        self._check_budget(ctx)
        return list(await asyncio.gather(*(self.run(spec, ctx, text) for spec, text in jobs)))


#: The process-wide runtime. Tests substitute one carrying a fake model.
runtime = OpenAIAgentsRuntime()
