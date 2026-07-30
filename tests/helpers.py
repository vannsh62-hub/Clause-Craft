"""Shared fixtures-as-functions for the sub-agent and orchestrator tests."""

from __future__ import annotations

import json

from agents.tool_context import ToolContext
from agents.usage import Usage

from backend.clauselib.serialise import loads_rendered
from backend.core.run_context import RunContext
from backend.workspace.models import ContractVersion
from backend.workspace.store import WorkspaceStore

NDA_VARS = {
    "disclosing_party": "ABC Pvt Ltd",
    "receiving_party": "XYZ Pvt Ltd",
    "duration_years": "3",
    "effective_date": "1 August 2026",
    "term_end_date": "1 August 2029",
    "governing_law_country": "India",
    "jurisdiction_city": "Mumbai",
    "disclosing_signatory": "Jane Rao",
    "receiving_signatory": "Sam Patel",
}


def tool_ctx(ctx: RunContext, name: str) -> ToolContext[RunContext]:
    return ToolContext(
        context=ctx, usage=Usage(), tool_name=name, tool_call_id="c1", tool_arguments="{}"
    )


async def render_clauses_for(ctx: RunContext) -> None:
    from backend.tools.clause_tool import render_clauses

    pairs = [{"name": k, "value": v} for k, v in NDA_VARS.items()]
    await render_clauses.on_invoke_tool(
        tool_ctx(ctx, "render_clauses"),
        json.dumps({"contract_type": "nda", "variables": pairs}),
    )


async def faithful_markdown(ctx: RunContext) -> str:
    """A draft that reproduces every approved clause verbatim, so the gates pass."""
    async with ctx.session_factory() as s:
        store = WorkspaceStore(s)
        paths = [f.path for f in await store.ls(ctx.contract_id) if f.read_only]
        clauses = sorted(
            [loads_rendered(await store.read(ctx.contract_id, p)) for p in paths],
            key=lambda c: c.order,
        )
    return "\n\n".join(f"## {c.title}\n\n{c.text}" for c in clauses)


async def seed_clean_draft(ctx: RunContext, attempt: int = 1) -> str:
    """Render the clauses and record a faithful draft as `attempt` in the ledger."""
    await render_clauses_for(ctx)
    markdown = await faithful_markdown(ctx)
    path = f"draft_v{attempt}.md"

    async with ctx.session_factory() as s:
        await WorkspaceStore(s).write(ctx.contract_id, path, markdown)
        s.add(
            ContractVersion(
                contract_id=ctx.contract_id, attempt=attempt, path=path, markdown=markdown
            )
        )
        await s.commit()
    return markdown
