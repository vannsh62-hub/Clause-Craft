"""The validation tool.

Reloads the rendered clauses from the read-only workspace — not from anything the drafting
agent said — and runs the three deterministic gates against a draft. Zero tokens.

Returns a report rather than raising: a blocked draft is normal control flow, and the agent
is meant to read the findings and fix it. See `backend/schemas/errors.py`.
"""

from __future__ import annotations

from agents import RunContextWrapper, function_tool

from backend.clauselib.loader import required_clause_ids
from backend.clauselib.serialise import loads_rendered
from backend.core.run_context import RunContext
from backend.invariants.validate import score_draft, validate_draft
from backend.schemas.clause import RenderedClause
from backend.schemas.errors import ClauseError, format_tool_error
from backend.tools.guard import loop_guard
from backend.workspace.store import READ_ONLY_PREFIX, WorkspaceStore

__all__ = ["load_rendered_clauses", "validate_draft_tool"]


async def load_rendered_clauses(ctx: RunContext) -> tuple[RenderedClause, ...]:
    """Read back exactly what `render_clauses` wrote, provenance included."""
    async with ctx.session_factory() as session:
        store = WorkspaceStore(session)
        paths = [f.path for f in await store.ls(ctx.contract_id) if f.read_only]
        texts = [await store.read(ctx.contract_id, p) for p in paths]

    if not paths:
        raise ClauseError(f"no rendered clauses in {READ_ONLY_PREFIX} — call render_clauses first")
    return tuple(sorted((loads_rendered(t) for t in texts), key=lambda c: c.order))


@function_tool(failure_error_function=format_tool_error)
@loop_guard
async def validate_draft_tool(wrapper: RunContextWrapper[RunContext], draft_path: str) -> str:
    """Check a draft against the approved clause set. Costs nothing; run it before finalizing.

    Reports three kinds of blocker: a required clause missing, an unresolved placeholder, and
    approved clause text that was reworded. Any blocker caps the contract's score at 89.

    Args:
        draft_path: workspace path of the draft, e.g. `draft_v1.md`.
    """
    ctx = wrapper.context
    if ctx.contract_type is None:  # pragma: no cover - orchestrator sets this before drafting
        raise ClauseError("contract type is not yet decided; call list_clause_library first")

    async with ctx.session_factory() as session:
        markdown = await WorkspaceStore(session).read(ctx.contract_id, draft_path)

    expected = await load_rendered_clauses(ctx)
    required = required_clause_ids(ctx.contract_type, ctx.jurisdiction)
    report = validate_draft(markdown, expected, required)

    if report.ok:
        return (
            f"PASS — {draft_path} contains all {len(required)} required clauses verbatim. "
            f"Deterministic score {report.deterministic_points}/70; "
            f"best achievable total {score_draft(report)}."
        )

    lines = [f"BLOCKED — {draft_path} cannot be finalized. {len(report.blockers)} blocker(s):"]
    lines += [f"  [{f.dimension}] {f.message} → {f.fix_hint}" for f in report.blockers]
    lines.append(f"Score is capped at {score_draft(report)} until these are fixed.")
    return "\n".join(lines)
