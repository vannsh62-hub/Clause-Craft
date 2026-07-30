"""Workspace tools: the agent's filesystem.

Thin adapters over `WorkspaceStore`, which is where the invariants actually live. These
tools cannot weaken them: `write_file` refuses `clauses/` because the *store* refuses it.
"""

from __future__ import annotations

from agents import RunContextWrapper, function_tool

from backend.core.run_context import RunContext
from backend.schemas.errors import format_tool_error
from backend.tools.guard import loop_guard
from backend.workspace.store import WorkspaceStore


@function_tool(failure_error_function=format_tool_error)
@loop_guard
async def ls_files(wrapper: RunContextWrapper[RunContext]) -> str:
    """List every file in the contract workspace, with its size and whether it is read-only."""
    ctx = wrapper.context
    async with ctx.session_factory() as session:
        files = await WorkspaceStore(session).ls(ctx.contract_id)

    if not files:
        return "(workspace is empty)"
    return "\n".join(
        f"{f.path}  {f.size} bytes{'  [read-only]' if f.read_only else ''}" for f in files
    )


@function_tool(failure_error_function=format_tool_error)
@loop_guard
async def read_file(wrapper: RunContextWrapper[RunContext], path: str) -> str:
    """Read a workspace file.

    Args:
        path: workspace path, e.g. `draft_v1.md` or `clauses/nda.duration.md`.
    """
    ctx = wrapper.context
    async with ctx.session_factory() as session:
        return await WorkspaceStore(session).read(ctx.contract_id, path)


@function_tool(failure_error_function=format_tool_error)
@loop_guard
async def write_file(wrapper: RunContextWrapper[RunContext], path: str, content: str) -> str:
    """Create or overwrite a workspace file. Files under `clauses/` are read-only.

    Args:
        path: workspace path. Lowercase letters, digits and `. _ - /` only.
        content: the full new contents of the file.
    """
    ctx = wrapper.context
    async with ctx.session_factory() as session:
        info = await WorkspaceStore(session).write(ctx.contract_id, path, content)
        await session.commit()
    return f"wrote {info.path} ({info.size} bytes, v{info.version})"


@function_tool(failure_error_function=format_tool_error)
@loop_guard
async def edit_file(wrapper: RunContextWrapper[RunContext], path: str, old: str, new: str) -> str:
    """Replace exactly one occurrence of `old` with `new` in a workspace file.

    Fails if `old` is absent, or if it appears more than once — include enough surrounding
    context that it matches exactly once.

    Args:
        path: workspace path.
        old: the exact text to replace.
        new: the replacement text.
    """
    ctx = wrapper.context
    async with ctx.session_factory() as session:
        info = await WorkspaceStore(session).edit(ctx.contract_id, path, old, new)
        await session.commit()
    return f"edited {info.path} (v{info.version})"
