"""The execution port.

One interface, one method that matters. An adapter takes an `AgentSpec`, runs it on some
engine, and returns an `AgentResult` whose `output` has already been validated by
`backend/runtime/parse.py`.

`run_many` is on the port rather than left to callers because *how* you run agents
concurrently is runtime-specific — `asyncio.gather` over independent runs here, a graph
fan-out elsewhere — while the requirement that they run concurrently is a property of the
pipeline. Spec 05 §6.5 needs three agents over one parsed document in parallel; the
pipeline should express that once and not care which engine is underneath.

Errors are normalised to the existing framework-free vocabulary in
`backend/schemas/errors.py` rather than to new runtime-specific types:

    SuspendRun            the run asked the user something and must pause
    CostCeilingExceeded   the token budget is spent
    ContractToolError     the agent ran away or returned something unusable

Adapters own the translation. The SDK in use today wraps a tool's exception in `UserError`,
so its adapter walks `__cause__` to recover the signal; that is an artefact of one library
and must not appear above the adapter boundary.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from backend.core.run_context import RunContext
from backend.runtime.spec import AgentResult, AgentSpec, HistoryHandle, Out

__all__ = ["AgentRuntime"]


class AgentRuntime(Protocol):
    """Executes `AgentSpec`s. The only thing that knows which engine is in use."""

    #: Identifies the engine in traces and logs, e.g. "openai_agents".
    name: str

    async def run(
        self,
        spec: AgentSpec[Out],
        ctx: RunContext,
        instruction: str,
        *,
        history: HistoryHandle | None = None,
    ) -> AgentResult[Out]:
        """Run one agent to completion.

        `history=None` means a fresh context window — the isolation that keeps a judge
        from seeing its own prior score and a sub-agent from seeing the supervisor's
        reasoning. Adapters must honour it structurally, never by prompting.

        Implementations must charge usage back onto `ctx` before returning, and must
        refuse to start when the budget is already spent.
        """
        ...

    async def run_many(
        self,
        jobs: Sequence[tuple[AgentSpec[Any], str]],
        ctx: RunContext,
    ) -> list[AgentResult[Any]]:
        """Run several agents concurrently, each isolated, results in `jobs` order.

        Callers must not have these agents write to the workspace concurrently: writes
        take a per-contract advisory lock, so parallel writers serialise at best and
        deadlock against a caller-held transaction at worst. Return the objects and let
        the caller persist them.
        """
        ...
