"""Date arithmetic as a tool, so the model never does it.

A model asked to add three years to 29 February 2028 is usually right. A contract term
cannot be "usually" right.
"""

from __future__ import annotations

from agents import RunContextWrapper, function_tool

from backend.core.run_context import RunContext
from backend.invariants.dates import contract_dates
from backend.schemas.errors import ContractToolError, format_tool_error
from backend.tools.guard import loop_guard


@function_tool(failure_error_function=format_tool_error)
@loop_guard
async def calculate_dates(
    wrapper: RunContextWrapper[RunContext], effective_date: str, duration: str
) -> dict[str, str]:
    """Compute the term end date and the display forms of both dates.

    Use the returned values as clause variables. Do not compute dates yourself.

    Args:
        effective_date: `YYYY-MM-DD` or `1 August 2026`.
        duration: e.g. `3 years`, `18 months`, `90 days`.
    """
    try:
        return dict(contract_dates(effective_date, duration))
    except ValueError as exc:
        raise ContractToolError(str(exc)) from exc
