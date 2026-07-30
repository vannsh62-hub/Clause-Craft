"""The tool registry.

Two rules hold for every tool in this file, and both were learned the hard way:

1. **Every tool sets `failure_error_function=format_tool_error`.** The SDK's default
   formatter echoes `str(exc)` to the model, which is exactly the leak we guard against.
   `assert_error_handlers_are_explicit` below fails the build if a tool forgets.

2. **Nothing here is an invariant.** These are thin adapters. `write_file` refuses `clauses/`
   because `WorkspaceStore` refuses it, not because this file remembers to check.

`ask_user` (M7) is the single exception to rule 1: it registers `failure_error_function=None`
so `SuspendRun` propagates past `Runner.run` into the run loop.
"""

from __future__ import annotations

from agents import FunctionTool

from backend.tools.clause_tool import list_clause_library, render_clauses
from backend.tools.date_tool import calculate_dates
from backend.tools.planning_tool import read_todos, write_todos
from backend.tools.validation_tool import validate_draft_tool
from backend.tools.workspace_tools import edit_file, ls_files, read_file, write_file

__all__ = [
    "ORCHESTRATOR_TOOLS",
    "assert_error_handlers_are_explicit",
    "drafting_tools",
    "judge_tools",
]

#: The orchestrator's full tool set. Sub-agents get narrower ones — a drafting agent that
#: can call `write_todos` is an orchestrator by another name.
ORCHESTRATOR_TOOLS: tuple[FunctionTool, ...] = (
    write_todos,
    read_todos,
    ls_files,
    read_file,
    write_file,
    edit_file,
    list_clause_library,
    render_clauses,
    calculate_dates,
    validate_draft_tool,
)


def drafting_tools() -> tuple[FunctionTool, ...]:
    """The drafting sub-agent reads clauses and writes its draft. It plans nothing."""
    return (read_file, ls_files, write_file, calculate_dates)


def judge_tools() -> tuple[FunctionTool, ...]:
    """The judge reads. It never writes, and it never re-renders clauses."""
    return (read_file, ls_files)


def assert_error_handlers_are_explicit(tools: tuple[FunctionTool, ...]) -> None:
    """Fail loudly if a tool would fall back to the SDK's leaky default error formatter.

    The SDK flags this with `_use_default_failure_error_function`, not by storing the default
    function itself — a tool built without the argument has `_failure_error_function is None`,
    which is indistinguishable from `failure_error_function=None` by identity alone.

    An explicit `None` is legal and deliberate: it is how `ask_user` lets `SuspendRun` escape.
    """
    for tool in tools:
        if getattr(tool, "_use_default_failure_error_function", False):
            raise AssertionError(
                f"{tool.name} uses the SDK default failure_error_function, which echoes "
                "str(exc) to the model. Pass failure_error_function=format_tool_error."
            )
