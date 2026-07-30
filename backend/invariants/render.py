"""Clause rendering. An invariant of the system.

The model never authors clause text. Approved clause bodies are rendered here, by a
template engine, from structured variables. That is what makes the provenance guarantee
- every clause in a draft traces to `clause_id@version` - enforceable rather than hoped for.

`StrictUndefined` is not a style choice. Under the default `Undefined`, a missing
`receiving_party` renders as the empty string and produces a syntactically fine contract
with a nameless counterparty. Here it raises.

This module imports neither `agents` nor `openai`, and it must stay that way: the
correctness layer cannot acquire an LLM dependency if the SDK is never in scope.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from jinja2 import Environment, StrictUndefined
from jinja2 import UndefinedError as JinjaUndefinedError

from backend.schemas.clause import Clause, RenderedClause

__all__ = ["MissingVariableError", "body_sha", "render_clause"]


class MissingVariableError(Exception):
    """A clause variable was not supplied. Never rendered blank."""


# autoescape is off: these are Markdown legal texts, not HTML. Escaping would corrupt
# ampersands and quotes in clause bodies. There is no HTML sink downstream.
_ENV = Environment(
    autoescape=False,  # noqa: S701 - Markdown legal text, not HTML; no HTML sink downstream
    undefined=StrictUndefined,
    keep_trailing_newline=False,
    trim_blocks=False,
)


def body_sha(body: str) -> str:
    """sha256 of the raw clause template, before substitution."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def render_clause(clause: Clause, variables: Mapping[str, str]) -> RenderedClause:
    """Substitute `variables` into `clause.body`.

    Raises `MissingVariableError` if any variable the template needs was not supplied.
    """
    missing = set(clause.variables) - set(variables)
    if missing:
        raise MissingVariableError(
            f"{clause.id}: missing {sorted(missing)}. "
            "Supply the value or call ask_user; a clause is never rendered blank."
        )

    try:
        text = _ENV.from_string(clause.body).render(**variables)
    except JinjaUndefinedError as exc:  # defence in depth; the check above should catch it
        raise MissingVariableError(f"{clause.id}: {exc}") from exc

    return RenderedClause(
        clause_id=clause.id,
        version=clause.version,
        title=clause.title,
        order=clause.order,
        text=text.strip(),
        source_sha=body_sha(clause.body),
    )
