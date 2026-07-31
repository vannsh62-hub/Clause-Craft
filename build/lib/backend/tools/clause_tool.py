"""Clause tools.

`render_clauses` is where the provenance guarantee is enforced at the tool boundary: the
approved text is rendered by Jinja2 and written into the read-only `clauses/` area. The
agent reads it. No model authors a word of it.
"""

from __future__ import annotations

from agents import RunContextWrapper, function_tool

from backend.clauselib.loader import ClauseLibraryError, clauses_for, contract_types
from backend.clauselib.serialise import clause_path, dumps_rendered
from backend.core.run_context import RunContext
from backend.invariants.render import MissingVariableError, render_clause
from backend.schemas.clause import ClauseVariable
from backend.schemas.errors import ClauseError, format_tool_error
from backend.tools.guard import loop_guard
from backend.workspace.models import Contract
from backend.workspace.store import WorkspaceStore


@function_tool(failure_error_function=format_tool_error)
@loop_guard
async def list_clause_library(wrapper: RunContextWrapper[RunContext], contract_type: str) -> str:
    """List the approved clauses for a contract type, in the order they must appear.

    Returns nothing for a contract type with no approved clause set — decline the request
    rather than drafting from memory.

    Args:
        contract_type: for example `nda` or `service`.
    """
    clauses = clauses_for(contract_type, wrapper.context.jurisdiction)
    if not clauses:
        known = ", ".join(sorted(contract_types()))
        raise ClauseError(
            f"no approved clause set for '{contract_type}'. Known types: {known}. "
            "Tell the user this contract type is not supported; do not draft one."
        )

    lines = [
        f"{c.order:>3}  {c.id:<24} {'required' if c.required else 'optional'}  "
        f"vars: {', '.join(c.variables) or '(none)'}"
        for c in clauses
    ]
    return "\n".join(lines)


@function_tool(failure_error_function=format_tool_error)
@loop_guard
async def render_clauses(
    wrapper: RunContextWrapper[RunContext],
    contract_type: str,
    variables: list[ClauseVariable],
) -> str:
    """Render the approved clauses with the supplied values and place them in `clauses/`.

    The rendered text is authoritative. Reproduce it verbatim in your draft; do not reword
    it. If a required value is missing this fails and names it — call `ask_user`.

    Args:
        contract_type: for example `nda` or `service`.
        variables: one entry per clause variable, e.g. `{"name": "receiving_party",
            "value": "XYZ Pvt Ltd"}`. Call `list_clause_library` to see what is needed.
    """
    ctx = wrapper.context
    try:
        clauses = clauses_for(contract_type, ctx.jurisdiction)
    except ClauseLibraryError as exc:  # pragma: no cover - library is validated at import
        raise ClauseError(str(exc)) from exc

    if not clauses:
        raise ClauseError(f"no approved clause set for '{contract_type}'")

    values = {v.name: v.value for v in variables}
    needed = {v for c in clauses for v in c.variables}
    if missing := sorted(needed - set(values)):
        raise ClauseError(
            f"cannot render {contract_type}: missing {missing}. "
            "Call ask_user for these values; a clause is never rendered blank."
        )

    rendered = []
    for clause in clauses:
        try:
            rendered.append(render_clause(clause, values))
        except MissingVariableError as exc:
            raise ClauseError(str(exc)) from exc

    async with ctx.session_factory() as session:
        store = WorkspaceStore(session)
        for rc in rendered:
            await store.put_clause(ctx.contract_id, clause_path(rc.clause_id), dumps_rendered(rc))

        # Rendering is the point at which the contract type stops being a guess. Record it on
        # the contract row as well as the run context: the next slice builds a fresh
        # RunContext, and `validate_draft` and the judge both need to know what to require.
        contract = await session.get(Contract, ctx.contract_id)
        if contract is not None:
            contract.contract_type = contract_type
            contract.jurisdiction = ctx.jurisdiction
            # Persist the resolved values too (disclosing_party, receiving_party, dates,
            # etc.) — this is the only place they're gathered, and the insert-clause
            # feature relies on `contract.variables` to auto-fill clauses added later in
            # the UI. Merge rather than replace: earlier slices may have set values that
            # this call didn't touch.
            contract.variables = {**contract.variables, **values}
        await session.commit()

    ctx.contract_type = contract_type

    listing = "\n".join(f"  {clause_path(rc.clause_id)}  ({rc.provenance})" for rc in rendered)
    return f"rendered {len(rendered)} clauses into the read-only workspace:\n{listing}"
