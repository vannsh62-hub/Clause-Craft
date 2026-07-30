"""Tool outcomes: return vs. raise.

The OpenAI Agents SDK offers no `ToolError` return type. A `@function_tool` either returns
a value — serialised to the model as the tool output — or raises, in which case
`failure_error_function` turns the exception into a model-facing string. When that hook is
`None`, the SDK re-raises past `Runner.run` into our own code (`agents/tool.py`: *"if result
is None: raise"*).

Three categories, three mechanisms. Getting this wrong is not cosmetic: raise a `Blocked`
draft and its structured findings are flattened into an error string the model must parse;
return a `SuspendRun` and the run never suspends.

| Category                                       | Mechanism                                |
|------------------------------------------------|------------------------------------------|
| Expected outcome the model should reason about | **return** `Blocked`                     |
| Fault the model may recover from               | **raise** `ContractToolError`            |
| Control signal that must reach *our* code      | **raise** `ControlSignal`, `failure=None` |

Rule of thumb: *retryable branch → return `Blocked`; recoverable fault → raise
`ContractToolError`; must-halt-the-run → raise `ControlSignal`.*
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict

from backend.schemas.draft import Finding

if TYPE_CHECKING:  # pragma: no cover - import only for typing; keeps this module SDK-free
    from agents import RunContextWrapper

__all__ = [
    "MAX_TOOL_ERROR_CHARS",
    "AttemptsExhausted",
    "Blocked",
    "ClauseError",
    "ContractToolError",
    "ControlSignal",
    "CostCeilingExceeded",
    "LoopDetected",
    "SuspendRun",
    "WorkspaceError",
    "format_tool_error",
]

#: Model-facing error strings are capped. A stack trace or a dumped SQL statement in the
#: context window is both a leak and a waste of tokens.
MAX_TOOL_ERROR_CHARS = 400


# --------------------------------------------------------------------- returned outcomes


class Blocked(BaseModel):
    """An expected, retryable outcome — *returned*, never raised.

    A draft that fails the deterministic gates is normal control flow, not an error. The
    agent is meant to read the findings and fix the draft. Raising would flatten them into
    a string.
    """

    model_config = ConfigDict(frozen=True)

    status: Literal["blocked"] = "blocked"
    kind: Literal["validation", "attempts_exhausted", "unsupported_contract_type"]
    hint: str
    findings: tuple[Finding, ...] = ()


# ------------------------------------------------------------------------------- faults


class ContractToolError(Exception):
    """A fault the agent may be able to work around.

    Raised by a tool; `format_tool_error` renders it for the model. Messages are written to
    be read by a model: state what went wrong and what to do instead.
    """


class ClauseError(ContractToolError):
    """An unknown clause id, or a clause that cannot be rendered."""


class WorkspaceError(ContractToolError):
    """An illegal workspace operation: bad path, unknown file, or a write into `clauses/`."""


class AttemptsExhausted(ContractToolError):
    """A fourth drafting attempt was requested. Enforced server-side, not by the prompt."""


class LoopDetected(ContractToolError):
    """The same tool was called with the same arguments too many times."""


# ------------------------------------------------------------------- control signals


class ControlSignal(Exception):
    """Must reach our run loop, never the model.

    Tools raising these are registered with `failure_error_function=None` so the SDK
    re-raises past `Runner.run` instead of formatting a message for the model.

    Derives from `Exception`, not `BaseException`, deliberately: the SDK's tool wrapper
    catches `Exception`, and only then consults `failure_error_function`. A `BaseException`
    would skip that machinery and escape by a different, untested path.
    """


class SuspendRun(ControlSignal):
    """`ask_user` was called. Persist, emit `input_required`, and end the run slice.

    `call_id` is the load-bearing field. When this propagates, the SDK has *already*
    appended the assistant's `ask_user` tool-call to the session with no matching tool
    output. On resume we must inject a tool-output item paired to this exact `call_id`, or
    the provider rejects the next completion.
    """

    def __init__(self, call_id: str, questions: list[dict[str, Any]]) -> None:
        super().__init__(f"run suspended awaiting {len(questions)} answer(s)")
        self.call_id = call_id
        self.questions = questions


class CostCeilingExceeded(ControlSignal):
    def __init__(self, spent_cents: float, ceiling_cents: float) -> None:
        super().__init__(f"cost ceiling exceeded: {spent_cents:.1f}c of {ceiling_cents:.1f}c")
        self.spent_cents = spent_cents
        self.ceiling_cents = ceiling_cents


# ------------------------------------------------------------- the shared error formatter


_GENERIC = (
    "The tool failed with an internal error ({kind}). Do not retry with identical "
    "arguments; try a different approach or ask the user."
)


def format_tool_error(ctx: RunContextWrapper[Any], error: Exception) -> str:
    """Render an exception as a terse, actionable, secret-free line for the model.

    Only *our* exception messages are echoed. Everything else is reported by class name
    only, because `str(exc)` on a third-party exception routinely embeds things that must
    not enter the context window or the trace exporter: a SQLAlchemy error carries the full
    statement and its bound parameters — which is to say the contract text — and a
    connection error carries the database URL, password included.
    """
    if isinstance(error, ControlSignal):  # pragma: no cover - registered with None instead
        raise error

    if isinstance(error, ContractToolError):
        message = f"{type(error).__name__}: {error}"
    else:
        message = _GENERIC.format(kind=type(error).__name__)

    message = " ".join(message.split())
    if len(message) > MAX_TOOL_ERROR_CHARS:
        message = message[: MAX_TOOL_ERROR_CHARS - 1].rstrip() + "…"
    return message
