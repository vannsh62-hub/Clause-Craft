"""`ask_user` — the tool that suspends the run.

`Runner.run` is one coroutine and cannot pause across an HTTP boundary. No process may block
on a human. So `ask_user` persists what it needs, raises `SuspendRun`, and the run slice ends.
`POST /answers` starts a *new* slice with the answers as a user message.

Two facts about this SDK, established by experiment rather than assumed, and pinned by
`tests/test_suspend_resume.py` so a version bump cannot quietly break them:

1. **`SuspendRun` does not reach us as itself.** `failure_error_function=None` re-raises it out
   of the tool, and the runner then wraps it: `raise UserError(f"Error running tool ...") from e`.
   Callers must unwrap `__cause__`. See `unwrap_control_signal`.

2. **There is no orphaned tool call.** The suspending turn aborts before its model response is
   written to the session, so the session ends with the last *completed* turn. Earlier turns —
   the plan the agent wrote, the clauses it rendered — do survive. Injecting a
   `function_call_output` on resume would therefore create the broken session it was meant to
   avoid: an output with no matching call.
"""

from __future__ import annotations

from agents import RunContextWrapper, function_tool
from agents.exceptions import UserError

from backend.core.run_context import RunContext
from backend.runtime.human import HumanGateway, SuspendingGateway
from backend.schemas.errors import ControlSignal
from backend.schemas.question import Question

__all__ = ["GATEWAY", "ask_user", "unwrap_control_signal"]

#: How this deployment obtains an answer. `SuspendingGateway` never returns — it ends the
#: slice. A runtime with durable pause/resume would supply a gateway that does return, and
#: `tests/test_human_gateway_contract.py` proves this tool is correct under both.
GATEWAY: HumanGateway = SuspendingGateway()


def unwrap_control_signal(exc: BaseException) -> ControlSignal | None:
    """Dig a `ControlSignal` out of whatever the runner wrapped it in."""
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, ControlSignal):
            return seen
        seen = seen.__cause__
    return None


def is_wrapped_control_signal(exc: BaseException) -> bool:
    return isinstance(exc, UserError) and unwrap_control_signal(exc) is not None


@function_tool(failure_error_function=None)
async def ask_user(wrapper: RunContextWrapper[RunContext], questions: list[Question]) -> str:
    """Ask the user for information you do not have, then stop. The run resumes when they answer.

    Ask for everything you need in **one** call. Never guess a date, a party name, a fee, a
    jurisdiction or a term length — an invented value in a contract is the worst thing this
    system can do. Do not ask for anything you can already read from the workspace.

    Args:
        questions: everything you need, each with the clause variable it answers.
    """
    if not questions:
        raise ValueError("ask_user needs at least one question")

    context = wrapper.context
    # `wrapper` is a ToolContext at runtime; this is the id of *this* tool call.
    call_id = str(getattr(wrapper, "tool_call_id", "") or "")

    # MAY NOT RETURN. Everything this tool needs is already committed by the gateway, and
    # nothing may follow this call — under a suspending gateway the stack unwinds here.
    answers = await GATEWAY.ask(context.session_factory, context.contract_id, questions, call_id)
    return "\n".join(f"{name}: {value}" for name, value in answers.items())
