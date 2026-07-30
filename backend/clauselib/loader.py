"""Loads and validates the approved clause library.

The library is a directory of Markdown files with YAML frontmatter, versioned in git.
Git is the approval audit trail: changing a clause is a pull request with a reviewer.

The loader is strict on purpose. A duplicate id, an ordering collision, or frontmatter
that disagrees with the template body are all defects that would surface much later as a
malformed contract, so they fail here with a message naming the offending files.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import frontmatter
from jinja2 import Environment, meta
from jinja2.exceptions import TemplateSyntaxError

from backend.schemas.clause import Clause

CLAUSE_ROOT = Path(__file__).resolve().parent.parent.parent / "clauses"

_REQUIRED_KEYS = frozenset(
    {"id", "version", "title", "contract_types", "jurisdictions", "required", "order", "variables"}
)


class ClauseLibraryError(Exception):
    """The clause library on disk is malformed. Raised at load, never at draft time."""


def _template_variables(body: str) -> set[str]:
    """The `{{ variable }}` names a clause body declares.

    A body is a Jinja template, so a malformed placeholder is a syntax error. The most
    common one by far is a space inside the name — `{{Vendor Name}}` — which is exactly what
    someone writes by hand. Translated into a `ClauseLibraryError` so the caller reports a
    usable message instead of failing with a 500: the clause library is edited through the
    UI now, and a raw template exception there is unreadable.
    """
    env = Environment(autoescape=False)  # noqa: S701 - renders Markdown, not HTML
    try:
        return meta.find_undeclared_variables(env.parse(body))
    except TemplateSyntaxError as exc:
        raise ClauseLibraryError(
            f"the clause text has an invalid placeholder (line {exc.lineno}): {exc.message}. "
            "A placeholder must be a single name with underscores rather than spaces — "
            "write {{ vendor_name }}, not {{Vendor Name}}."
        ) from exc


def _str_list(path: Path, key: str, value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ClauseLibraryError(f"{path}: frontmatter '{key}' must be a list of strings")
    return [str(v) for v in value]


def _parse_clause(path: Path, contract_type: str, base: Path) -> Clause:
    post = frontmatter.load(path)
    md: dict[str, Any] = dict(post.metadata)

    if missing := _REQUIRED_KEYS - set(md):
        raise ClauseLibraryError(f"{path}: frontmatter is missing {sorted(missing)}")

    clause_id = str(md["id"])
    if not clause_id.startswith(f"{contract_type}."):
        raise ClauseLibraryError(
            f"{path}: id '{clause_id}' does not match its directory; "
            f"expected it to start with '{contract_type}.'"
        )

    types = _str_list(path, "contract_types", md["contract_types"])
    jurisdictions = _str_list(path, "jurisdictions", md["jurisdictions"])

    if contract_type not in types:
        raise ClauseLibraryError(
            f"{path}: lives under clauses/{contract_type}/ but declares contract_types={types}"
        )

    body = post.content.strip()
    declared = set(_str_list(path, "variables", md["variables"]))
    used = _template_variables(body)

    if undeclared := used - declared:
        raise ClauseLibraryError(
            f"{path}: template uses {sorted(undeclared)} but does not declare them. "
            "Undeclared variables render blank and would silently corrupt a contract."
        )
    if unused := declared - used:
        raise ClauseLibraryError(
            f"{path}: declares {sorted(unused)} but the template never uses them"
        )

    risk = str(md.get("risk", "medium")).strip().lower()
    if risk not in {"low", "medium", "high"}:
        risk = "medium"

    return Clause(
        id=clause_id,
        version=int(md["version"]),
        title=str(md["title"]),
        contract_types=tuple(types),
        jurisdictions=tuple(jurisdictions),
        required=bool(md["required"]),
        order=int(md["order"]),
        variables=tuple(sorted(declared)),
        body=body,
        source_path=f"{base.name}/{path.relative_to(base)}",
        risk=risk,
    )


def _reject_duplicate_ids(clauses: list[Clause]) -> None:
    seen: dict[str, str] = {}
    for c in clauses:
        if c.id in seen:
            raise ClauseLibraryError(
                f"duplicate clause id '{c.id}' in {seen[c.id]} and {c.source_path}"
            )
        seen[c.id] = c.source_path


def _reject_order_collisions(clauses: list[Clause]) -> None:
    by_type: dict[tuple[str, int], str] = {}
    for c in clauses:
        for ct in c.contract_types:
            key = (ct, c.order)
            if key in by_type:
                raise ClauseLibraryError(
                    f"order {c.order} is claimed twice for contract type '{ct}': "
                    f"{by_type[key]} and {c.source_path}. Clause order must be total."
                )
            by_type[key] = c.source_path


@lru_cache(maxsize=1)
def load_library(root: Path | None = None) -> tuple[Clause, ...]:
    """Parse and validate every clause. Cached; the library is immutable at runtime."""
    base = root or CLAUSE_ROOT
    if not base.is_dir():
        raise ClauseLibraryError(f"clause library not found at {base}")

    clauses: list[Clause] = []
    for type_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        for path in sorted(type_dir.glob("*.md")):
            clauses.append(_parse_clause(path, type_dir.name, base))

    if not clauses:
        raise ClauseLibraryError(f"clause library at {base} is empty")

    _reject_duplicate_ids(clauses)
    _reject_order_collisions(clauses)
    return tuple(sorted(clauses, key=lambda c: (c.contract_types[0], c.order)))


def contract_types() -> frozenset[str]:
    return frozenset(ct for c in load_library() for ct in c.contract_types)


def clauses_for(contract_type: str, jurisdiction: str = "IN") -> tuple[Clause, ...]:
    """Every clause for a contract type, in library order.

    Deterministic filtered lookup, not embedding search. The library is a dozen documents;
    exact lookup is faster, free, reproducible, and more accurate than semantic similarity
    for "give me every required NDA clause". Returns () for an unknown type — the Planner
    turns that into `intent: unsupported` rather than improvising a contract.
    """
    return tuple(
        c
        for c in load_library()
        if contract_type in c.contract_types and jurisdiction in c.jurisdictions
    )


def required_clause_ids(contract_type: str, jurisdiction: str = "IN") -> frozenset[str]:
    """The clause ids a valid contract of this type must contain.

    This is pure library metadata, which is why it lives here rather than in retrieval:
    `invariants.validate` needs it for the completeness gate and must not depend on an
    agent-facing service to get it.
    """
    return frozenset(c.id for c in clauses_for(contract_type, jurisdiction) if c.required)


def get_clause(clause_id: str) -> Clause:
    for c in load_library():
        if c.id == clause_id:
            return c
    raise ClauseLibraryError(f"unknown clause id '{clause_id}'")
