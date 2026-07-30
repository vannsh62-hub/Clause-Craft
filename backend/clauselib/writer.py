"""Creating, editing, and removing clause library files.

The library is a directory of Markdown-with-frontmatter files. This module is the write side
of it — it serialises a clause to a `.md` file, validates the result by loading it back
through the same strict loader the pipeline uses, and invalidates the loader cache so the
change is visible immediately.

Two things are done *for* the caller rather than trusted from them, because getting them
wrong corrupts a contract silently:

- **Variables are derived from the body**, not supplied. A template that uses `{{ fee }}`
  must declare `fee`; declaring the wrong set renders blanks. So the declared set is computed
  from the body and the caller never manages it.
- **Every write is validated by re-parsing.** A file that would not load is rejected before
  it is committed, so the library on disk is always one the pipeline can read.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path

import frontmatter

from backend.clauselib.loader import (
    CLAUSE_ROOT,
    ClauseLibraryError,
    _parse_clause,
    _template_variables,
    get_clause,
    load_library,
)
from backend.schemas.clause import Clause

__all__ = ["delete_clause", "upsert_clause"]

_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")


def _invalidate() -> None:
    """The loader caches the whole library; a write makes that cache stale."""
    load_library.cache_clear()


def _path_for(clause_id: str, root: Path) -> Path:
    contract_type, name = clause_id.split(".", 1)
    return root / contract_type / f"{name}.md"


def _serialise(
    *,
    clause_id: str,
    title: str,
    contract_types: list[str],
    jurisdictions: list[str],
    required: bool,
    order: int,
    risk: str,
    body: str,
) -> str:
    """A clause as Markdown-with-frontmatter, variables derived from the body."""
    variables = sorted(_template_variables(body))
    post = frontmatter.Post(
        body.strip(),
        id=clause_id,
        version=1,
        title=title,
        contract_types=contract_types,
        jurisdictions=jurisdictions,
        required=required,
        order=order,
        variables=variables,
        risk=risk,
    )
    return frontmatter.dumps(post)


def upsert_clause(
    *,
    clause_id: str,
    title: str,
    contract_type: str,
    jurisdiction: str,
    required: bool,
    order: int,
    risk: str,
    body: str,
    root: Path | None = None,
) -> Clause:
    """Create or replace a clause, keeping its version incrementing on edit.

    The id must be `<contract_type>.<name>`, and its prefix must match the contract type it
    is filed under — the loader enforces this, and mixing them is how a clause ends up in the
    wrong drafting path. Editing an existing clause bumps its version, so the library keeps a
    sense of what changed.
    """
    base = root or CLAUSE_ROOT
    clause_id = clause_id.strip().lower()

    if not _ID.match(clause_id):
        raise ClauseLibraryError(
            f"clause id {clause_id!r} must be '<contract_type>.<name>', lowercase, "
            "letters/digits/underscores only"
        )
    if not clause_id.startswith(f"{contract_type}."):
        raise ClauseLibraryError(
            f"id {clause_id!r} must start with '{contract_type}.' to match its contract type"
        )
    if not body.strip():
        raise ClauseLibraryError("a clause needs a body")

    # Preserve and bump the version when editing an existing clause.
    version = 1
    with contextlib.suppress(ClauseLibraryError):
        version = get_clause(clause_id).version + 1

    text = _serialise(
        clause_id=clause_id,
        title=title.strip() or clause_id,
        contract_types=[contract_type],
        jurisdictions=[j.strip() for j in jurisdiction.split(",") if j.strip()] or ["IN"],
        required=required,
        order=order,
        risk=risk,
        body=body,
    )
    # Splice the bumped version in — `_serialise` always writes version 1.
    text = re.sub(r"^version:.*$", f"version: {version}", text, count=1, flags=re.MULTILINE)

    path = _path_for(clause_id, base)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Validate by parsing the serialised form before committing it, so a clause that would
    # not load never reaches the library. Written to a temp file first, promoted only if it
    # parses.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        _parse_clause(tmp, contract_type, base)
    except ClauseLibraryError:
        tmp.unlink(missing_ok=True)
        raise

    tmp.replace(path)
    _invalidate()
    # Re-read through the loader so the returned clause carries the canonical source_path.
    return get_clause(clause_id)


def delete_clause(clause_id: str, root: Path | None = None) -> None:
    """Remove a clause from the library. Idempotent-ish: an unknown id is an error."""
    base = root or CLAUSE_ROOT
    clause_id = clause_id.strip().lower()
    path = _path_for(clause_id, base)
    if not path.is_file():
        raise ClauseLibraryError(f"no clause {clause_id!r} to delete")
    path.unlink()
    _invalidate()
