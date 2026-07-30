from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from backend.clauselib.loader import (
    ClauseLibraryError,
    clauses_for,
    contract_types,
    get_clause,
    load_library,
    required_clause_ids,
)

# ---------------------------------------------------------------- the real library


def test_library_loads() -> None:
    lib = load_library()
    # Subset, not equality: the library is user-editable through the clause CRUD API, so a
    # clause added via the UI must not break this. What is asserted is that the shipped
    # baseline is present and loads — 6 required NDA + 1 optional NDA + 6 service.
    assert len(lib) >= 13
    assert {"nda", "service"} <= contract_types()


def test_nda_clauses_come_back_in_library_order() -> None:
    orders = [c.order for c in clauses_for("nda")]
    assert orders == sorted(orders)
    assert [c.id for c in clauses_for("nda")][:2] == ["nda.definitions", "nda.confidentiality"]


def test_lookup_is_deterministic() -> None:
    first = [c.id for c in clauses_for("nda")]
    for _ in range(50):
        assert [c.id for c in clauses_for("nda")] == first


def test_unknown_contract_type_returns_empty_not_raise() -> None:
    # The Planner turns this into intent="unsupported" rather than improvising a contract.
    assert clauses_for("employment") == ()
    assert required_clause_ids("employment") == frozenset()


def test_unknown_jurisdiction_returns_empty() -> None:
    assert clauses_for("nda", jurisdiction="US") == ()


def test_required_clause_ids_excludes_optional_clauses() -> None:
    required = required_clause_ids("nda")
    all_ids = {c.id for c in clauses_for("nda")}

    assert "nda.duration" in required
    assert "nda.non_solicitation" in all_ids
    assert "nda.non_solicitation" not in required, "optional clauses must not be required"
    assert required < all_ids, "a bug returning every id would pass a weaker assertion"


def test_get_clause_rejects_unknown_id() -> None:
    with pytest.raises(ClauseLibraryError, match="unknown clause id"):
        get_clause("nda.does_not_exist")


# ---------------------------------------------------------------- malformed libraries


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text).lstrip())


def _clause(cid: str, order: int, *, variables: str = "[a]", body: str = "Hello {{ a }}.") -> str:
    return f"""\
    ---
    id: {cid}
    version: 1
    title: T
    contract_types: [nda]
    jurisdictions: [IN]
    required: true
    order: {order}
    variables: {variables}
    ---

    {body}
    """


def test_duplicate_id_fails_and_names_both_files(tmp_path: Path) -> None:
    _write(tmp_path, "nda/a.md", _clause("nda.same", 10))
    _write(tmp_path, "nda/b.md", _clause("nda.same", 20))

    load_library.cache_clear()
    with pytest.raises(ClauseLibraryError) as exc:
        load_library(tmp_path)

    msg = str(exc.value)
    assert "nda/a.md" in msg and "nda/b.md" in msg
    load_library.cache_clear()


def test_order_collision_fails(tmp_path: Path) -> None:
    _write(tmp_path, "nda/a.md", _clause("nda.a", 10))
    _write(tmp_path, "nda/b.md", _clause("nda.b", 10))

    load_library.cache_clear()
    with pytest.raises(ClauseLibraryError, match="order 10 is claimed twice"):
        load_library(tmp_path)
    load_library.cache_clear()


def test_undeclared_template_variable_fails(tmp_path: Path) -> None:
    # An undeclared variable would render blank and silently corrupt a contract.
    _write(tmp_path, "nda/a.md", _clause("nda.a", 10, variables="[a]", body="{{ a }} {{ ghost }}"))

    load_library.cache_clear()
    with pytest.raises(ClauseLibraryError, match="ghost"):
        load_library(tmp_path)
    load_library.cache_clear()


def test_declared_but_unused_variable_fails(tmp_path: Path) -> None:
    _write(tmp_path, "nda/a.md", _clause("nda.a", 10, variables="[a, stale]", body="{{ a }}"))

    load_library.cache_clear()
    with pytest.raises(ClauseLibraryError, match="stale"):
        load_library(tmp_path)
    load_library.cache_clear()


def test_id_not_matching_directory_fails(tmp_path: Path) -> None:
    _write(tmp_path, "nda/a.md", _clause("service.a", 10))

    load_library.cache_clear()
    with pytest.raises(ClauseLibraryError, match="does not match its directory"):
        load_library(tmp_path)
    load_library.cache_clear()


def test_missing_frontmatter_key_fails(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "nda/a.md",
        """\
        ---
        id: nda.a
        version: 1
        ---

        Body.
        """,
    )
    load_library.cache_clear()
    with pytest.raises(ClauseLibraryError, match="missing"):
        load_library(tmp_path)
    load_library.cache_clear()


def test_empty_library_fails(tmp_path: Path) -> None:
    (tmp_path / "nda").mkdir()
    load_library.cache_clear()
    with pytest.raises(ClauseLibraryError, match="empty"):
        load_library(tmp_path)
    load_library.cache_clear()
