"""The planning tools. This is the deep-agent property made durable and inspectable.

The orchestrator maintains a todo list and *revises* it as facts change. Persisting it means
the UI can render a live checklist, and a reader can see the plan the agent actually held.
"""

from __future__ import annotations

from agents import RunContextWrapper, function_tool
from sqlalchemy import delete, select

from backend.core.run_context import RunContext
from backend.schemas.errors import format_tool_error
from backend.schemas.todo import Todo
from backend.workspace.models import AgentTodo


@function_tool(failure_error_function=format_tool_error)
async def write_todos(wrapper: RunContextWrapper[RunContext], todos: list[Todo]) -> str:
    """Record or revise your plan. Replaces the whole list, so send every step each time.

    Call this before you start work, and again whenever the facts change — a user answer
    that alters the contract type, a judge finding that needs a different fix.

    Args:
        todos: the ordered steps, each with a `task` and a `status`.
    """
    ctx = wrapper.context
    async with ctx.session_factory() as session:
        await session.execute(delete(AgentTodo).where(AgentTodo.contract_id == ctx.contract_id))
        session.add_all(
            AgentTodo(contract_id=ctx.contract_id, seq=i, task=t.task, status=t.status)
            for i, t in enumerate(todos)
        )
        await session.commit()

    done = sum(1 for t in todos if t.status == "done")
    return f"plan saved: {len(todos)} steps, {done} done"


@function_tool(failure_error_function=format_tool_error)
async def read_todos(wrapper: RunContextWrapper[RunContext]) -> str:
    """Read back your current plan."""
    ctx = wrapper.context
    async with ctx.session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(AgentTodo)
                    .where(AgentTodo.contract_id == ctx.contract_id)
                    .order_by(AgentTodo.seq)
                )
            )
            .scalars()
            .all()
        )

    if not rows:
        return "(no plan yet — call write_todos first)"
    marks = {"pending": "[ ]", "in_progress": "[~]", "done": "[x]", "cancelled": "[-]"}
    return "\n".join(f"{marks[r.status]} {r.task}" for r in rows)
