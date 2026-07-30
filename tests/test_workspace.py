from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.schemas.errors import WorkspaceError
from backend.workspace.models import WorkspaceFile
from backend.workspace.store import WorkspaceStore


@pytest.fixture
def store(session: AsyncSession) -> WorkspaceStore:
    return WorkspaceStore(session)


# ---------------------------------------------------------------------- basic CRUD


async def test_write_then_read_round_trips(store: WorkspaceStore, contract_id: uuid.UUID) -> None:
    await store.write(contract_id, "draft_v1.md", "# NDA\n\nBody.")
    assert await store.read(contract_id, "draft_v1.md") == "# NDA\n\nBody."


async def test_overwrite_bumps_version(store: WorkspaceStore, contract_id: uuid.UUID) -> None:
    first = await store.write(contract_id, "plan.md", "one")
    second = await store.write(contract_id, "plan.md", "two")

    assert (first.version, second.version) == (1, 2)
    assert await store.read(contract_id, "plan.md") == "two"


async def test_ls_lists_paths_in_order(store: WorkspaceStore, contract_id: uuid.UUID) -> None:
    await store.write(contract_id, "draft_v1.md", "d")
    await store.write(contract_id, "plan.md", "p")
    await store.put_clause(contract_id, "clauses/nda.duration.md", "c")

    listed = await store.ls(contract_id)
    assert [f.path for f in listed] == ["clauses/nda.duration.md", "draft_v1.md", "plan.md"]
    assert [f.read_only for f in listed] == [True, False, False]


async def test_reading_a_missing_file_raises_with_a_hint(
    store: WorkspaceStore, contract_id: uuid.UUID
) -> None:
    with pytest.raises(WorkspaceError, match="no such file"):
        await store.read(contract_id, "nope.md")


async def test_exists(store: WorkspaceStore, contract_id: uuid.UUID) -> None:
    assert not await store.exists(contract_id, "plan.md")
    await store.write(contract_id, "plan.md", "x")
    assert await store.exists(contract_id, "plan.md")


# ------------------------------------------------------- invariant: clauses/ is read-only


async def test_agent_cannot_write_into_clauses(
    store: WorkspaceStore, contract_id: uuid.UUID
) -> None:
    """Asserted at the store level, so it holds no matter which tool calls it."""
    with pytest.raises(WorkspaceError, match="read-only"):
        await store.write(contract_id, "clauses/confidentiality.md", "rewritten by the model")


async def test_agent_cannot_edit_a_clause(store: WorkspaceStore, contract_id: uuid.UUID) -> None:
    await store.put_clause(contract_id, "clauses/nda.confidentiality.md", "strict confidence")

    with pytest.raises(WorkspaceError, match="read-only"):
        await store.edit(contract_id, "clauses/nda.confidentiality.md", "strict", "reasonable")


async def test_put_clause_is_the_only_way_into_the_read_only_area(
    store: WorkspaceStore, contract_id: uuid.UUID
) -> None:
    info = await store.put_clause(contract_id, "clauses/nda.duration.md", "The term is 3 years.")
    assert info.read_only is True

    with pytest.raises(WorkspaceError, match="must live under clauses/"):
        await store.put_clause(contract_id, "draft_v1.md", "not a clause")


async def test_a_read_only_row_is_protected_independently_of_its_path(
    store: WorkspaceStore, contract_id: uuid.UUID, session: AsyncSession
) -> None:
    """The prefix check guards the path; this guards the row.

    If READ_ONLY_PREFIX were ever changed, the path check would stop protecting existing
    rows. The row-level check means approved text stays approved regardless.
    """
    session.add(
        WorkspaceFile(
            contract_id=contract_id, path="draft_v1.md", content="approved", read_only=True
        )
    )
    await session.flush()

    with pytest.raises(WorkspaceError, match="read-only"):
        await store.write(contract_id, "draft_v1.md", "overwritten")

    assert await store.read(contract_id, "draft_v1.md") == "approved"


@pytest.mark.parametrize(
    "lookalike",
    [
        "Clauses/nda.md",  # case
        "./clauses/nda.md",  # relative prefix
        "clauses//nda.md",  # empty segment
        "foo/../clauses/nda.md",  # traversal-shaped
        "/clauses/nda.md",  # absolute
        "clauses/../clauses/nda.md",
        "CLAUSES/nda.md",
    ],
)
async def test_read_only_prefix_cannot_be_dodged_by_spelling(
    store: WorkspaceStore, contract_id: uuid.UUID, lookalike: str
) -> None:
    """The danger is not traversal — these are database keys. It is a lookalike file that
    slips past the prefix check and later reads back as though it were approved text."""
    with pytest.raises(WorkspaceError):
        await store.write(contract_id, lookalike, "pretending to be an approved clause")


# --------------------------------------------------------------- invariant: path hygiene


@pytest.mark.parametrize(
    "bad",
    ["", "a" * 513, "UPPER.md", "sp ace.md", "back\\slash.md", "nul\x00.md", "a/", "a//b", "a/./b"],
)
async def test_illegal_paths_are_refused(
    store: WorkspaceStore, contract_id: uuid.UUID, bad: str
) -> None:
    with pytest.raises(WorkspaceError):
        await store.write(contract_id, bad, "x")


@pytest.mark.parametrize("good", ["plan.md", "draft_v1.md", "findings_v10.json", "a-b_c.md"])
async def test_legal_paths_are_accepted(
    store: WorkspaceStore, contract_id: uuid.UUID, good: str
) -> None:
    assert (await store.write(contract_id, good, "x")).path == good


# --------------------------------------------------------- invariant: contract scoping


async def test_a_file_is_not_visible_from_another_contract(
    store: WorkspaceStore, contract_id: uuid.UUID, other_contract_id: uuid.UUID
) -> None:
    await store.write(contract_id, "draft_v1.md", "secret draft")

    with pytest.raises(WorkspaceError, match="no such file"):
        await store.read(other_contract_id, "draft_v1.md")

    assert await store.ls(other_contract_id) == ()


async def test_same_path_in_two_contracts_are_independent_files(
    store: WorkspaceStore, contract_id: uuid.UUID, other_contract_id: uuid.UUID
) -> None:
    await store.write(contract_id, "plan.md", "nda plan")
    await store.write(other_contract_id, "plan.md", "service plan")

    assert await store.read(contract_id, "plan.md") == "nda plan"
    assert await store.read(other_contract_id, "plan.md") == "service plan"


async def test_editing_cannot_reach_across_contracts(
    store: WorkspaceStore, contract_id: uuid.UUID, other_contract_id: uuid.UUID
) -> None:
    await store.write(contract_id, "draft_v1.md", "the liability clause applies")

    with pytest.raises(WorkspaceError, match="no such file"):
        await store.edit(other_contract_id, "draft_v1.md", "applies", "does not apply")


# ------------------------------------------------------------------------------- edit


async def test_edit_replaces_a_single_occurrence(
    store: WorkspaceStore, contract_id: uuid.UUID
) -> None:
    await store.write(contract_id, "draft_v1.md", "term is 2 years")
    await store.edit(contract_id, "draft_v1.md", "2 years", "3 years")

    assert await store.read(contract_id, "draft_v1.md") == "term is 3 years"


async def test_edit_refuses_an_ambiguous_match(
    store: WorkspaceStore, contract_id: uuid.UUID
) -> None:
    """A silent multi-replace in a contract is as bad as a silent no-op."""
    await store.write(contract_id, "draft_v1.md", "party. party. party.")

    with pytest.raises(WorkspaceError, match="appears 3 times"):
        await store.edit(contract_id, "draft_v1.md", "party", "counterparty")

    assert await store.read(contract_id, "draft_v1.md") == "party. party. party."


async def test_edit_refuses_a_missing_match(store: WorkspaceStore, contract_id: uuid.UUID) -> None:
    await store.write(contract_id, "draft_v1.md", "hello")

    with pytest.raises(WorkspaceError, match="not found"):
        await store.edit(contract_id, "draft_v1.md", "goodbye", "hello")


async def test_edit_refuses_an_empty_old_string(
    store: WorkspaceStore, contract_id: uuid.UUID
) -> None:
    await store.write(contract_id, "draft_v1.md", "hello")

    with pytest.raises(WorkspaceError, match="non-empty"):
        await store.edit(contract_id, "draft_v1.md", "", "x")
