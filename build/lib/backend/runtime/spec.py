"""Agents declared as data.

An `AgentSpec` says *what* an agent is — its prompt, model, tools, output shape, turn
limit — and nothing about *how* it runs. A runtime adapter takes the spec and executes
it. This is the whole of the runtime-agnostic argument: if agent definitions live in
plain dataclasses, swapping the execution engine touches the adapter and nothing else.

This module imports no agent framework, and `tests/test_invariants_are_llm_free.py`
asserts it. If `agents` or `langchain` ever appears here, the port has stopped being a
port.

Spec 05 §0.1 originally proposed migrating to `deepagents`. That migration is deferred —
the library requires Python >=3.13 against this project's >=3.10,<3.13, and the backend
APIs it would need (`CompositeBackend`, a read-only wrapper, `interrupt`) are unverified.
Nothing here assumes either runtime, so adopting it later is additive.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import BaseModel

__all__ = ["AgentResult", "AgentSpec", "HistoryHandle", "Out"]

Out = TypeVar("Out", bound=BaseModel)


@dataclass(frozen=True)
class HistoryHandle:
    """An opaque reference to a conversation transcript.

    Deliberately not a session object. Under `openai-agents` this resolves to a
    `SQLAlchemySession`; under a graph runtime it would be a thread id. Callers pass the
    handle or pass `None`, and `None` is the isolation primitive: a sub-agent given no
    history starts with a fresh context window and cannot see the supervisor's reasoning
    or its own prior output. That property is a correctness requirement, not a
    performance one — see `backend/subagents/common.py`.
    """

    kind: str
    key: str


@dataclass(frozen=True)
class AgentSpec(Generic[Out]):
    """A complete, runtime-neutral description of one agent.

    `prompt` is resolved text rather than a prompt name so that the spec is
    self-contained and can be hashed straight into a run trace — a trace is only
    replayable against the prompt that produced it.

    `tools` are names, resolved by the adapter against its own registry. Tool *bodies*
    stay where they are for now; M0 moves agent definition behind the port, not tool
    definition. The second runtime is what will justify declaring tool signatures
    abstractly, and there is no second runtime yet.
    """

    name: str
    prompt: str
    model: str
    tools: tuple[str, ...] = ()
    output_model: type[Out] | None = None
    max_turns: int = 10
    temperature: float = 0.2
    parallel_tool_calls: bool = False


@dataclass(frozen=True)
class AgentResult(Generic[Out]):
    """What an agent produced, plus what it cost.

    `output` is a validated model instance when the spec declares `output_model`, and
    `None` otherwise. Adapters must never return an unvalidated payload here; validation
    belongs to `backend/runtime/parse.py` so that every runtime is held to the same
    contract.

    Usage is reported so callers can charge sub-agent spend to the parent budget.
    Delegation that escapes the ceiling is an unbounded spend.
    """

    text: str
    output: Out | None
    input_tokens: int = 0
    output_tokens: int = 0
    model_requests: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens
