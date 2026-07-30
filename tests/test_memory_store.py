"""The memory store. No model, no tokens.

These are the rules that make memory safe to have at all: what may be remembered, what happens
when the user changes their mind, and what a fact must be before it is allowed to fill a field
without asking.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.core.config import settings
from backend.core.principal import Principal, current_principal
from backend.memory.models import MemoryFact
from backend.memory.stability import MEMORABLE, is_memorable
from backend.memory.store import Conflict, MemoryRefused, MemoryStore, Stored


@pytest_asyncio.fixture
async def store() -> AsyncIterator[MemoryStore]:
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    principal = Principal(tenant_id=uuid.uuid4(), user_id=uuid.uuid4())

    async with factory() as session:
        try:
            yield MemoryStore(session, principal)
        finally:
            # `try/finally`, not bare statements after the yield. pytest throws a failing
            # test's exception *into* the generator at the yield point, so cleanup written
            # after it is skipped exactly when it is most needed — one failing test would
            # otherwise leak its rows into every test that ran afterwards.
            await session.execute(
                delete(MemoryFact).where(MemoryFact.tenant_id == principal.tenant_id)
            )
            await session.commit()
    await engine.dispose()


# ------------------------------------------------------- what may be remembered at all


async def test_a_deal_particular_is_refused_at_the_store(store: MemoryStore) -> None:
    """The counterparty on your last NDA is not a default for your next one."""
    for key in ("effective_date", "receiving_party", "fee_amount", "services_description"):
        with pytest.raises(MemoryRefused, match="never remembered|not a memorable key"):
            await store.remember(key, "whatever")


async def test_an_unknown_key_is_refused_and_says_what_is_known(store: MemoryStore) -> None:
    with pytest.raises(MemoryRefused) as exc:
        await store.remember("favourite_colour", "blue")

    assert "my_company_name" in str(exc.value), "the refusal should name the allow-list"


async def test_the_allow_list_holds_no_deal_particulars() -> None:
    """A regression guard on the table itself: if someone adds `effective_date` to MEMORABLE,
    every contract silently inherits the last one's date."""
    forbidden = {
        "effective_date",
        "term_end_date",
        "disclosing_party",
        "receiving_party",
        "fee_amount",
        "liability_cap",
        "services_description",
        "disclosing_signatory",
        "receiving_signatory",
    }
    assert not (forbidden & set(MEMORABLE))
    assert not is_memorable("effective_date")


# --------------------------------------------------------------- confirmed vs inferred


async def test_a_confirmed_fact_may_fill_a_field_without_asking(store: MemoryStore) -> None:
    await store.remember("my_company_name", "ABC Pvt Ltd")

    hit = (await store.recall(["my_company_name"]))[0]
    assert hit.usable_without_asking
    assert hit.source == "user_confirmed"
    assert hit.confidence == 1.0


async def test_a_carried_forward_fact_is_a_question_not_an_answer(store: MemoryStore) -> None:
    """Inferred from a prior contract, never confirmed. The agent must still ask."""
    await store.remember("preferred_duration_years", "3", source="carried_forward", confidence=0.7)

    hit = (await store.recall(["preferred_duration_years"]))[0]
    assert not hit.usable_without_asking


async def test_a_carried_forward_fact_cannot_launder_itself_to_confidence_one(
    store: MemoryStore,
) -> None:
    with pytest.raises(MemoryRefused, match="not an answer"):
        await store.remember("my_signatory", "Jane Rao", source="carried_forward", confidence=1.0)


async def test_a_confirmed_fact_must_have_confidence_one(store: MemoryStore) -> None:
    with pytest.raises(MemoryRefused, match="confidence 1.0"):
        await store.remember("my_signatory", "Jane Rao", source="user_confirmed", confidence=0.8)


async def test_a_stale_fact_is_a_question_even_though_it_was_confirmed(
    store: MemoryStore,
) -> None:
    """Signatories leave. A fact past its half-life is re-confirmed, not reused."""
    await store.remember("my_signatory", "Jane Rao")

    # Scoped by tenant. An unscoped `scalar_one()` reads every tenant's rows, so it raises
    # MultipleResultsFound the moment any other test's fact with this key is in the table —
    # a failure in this test that is really a report about an unrelated one.
    fact = (
        await store._session.execute(
            select(MemoryFact).where(
                MemoryFact.tenant_id == store._principal.tenant_id,
                MemoryFact.key == "my_signatory",
            )
        )
    ).scalar_one()
    fact.stale_after = datetime.now(timezone.utc) - timedelta(days=1)
    await store._session.flush()

    hit = (await store.recall(["my_signatory"]))[0]
    assert hit.stale
    assert not hit.usable_without_asking, "a stale fact must not fill a field"


async def test_a_stable_fact_outlives_a_volatile_one(store: MemoryStore) -> None:
    await store.remember("my_company_name", "ABC Pvt Ltd")  # stable
    await store.remember("my_signatory", "Jane Rao")  # volatile

    rows = {
        r.key: r
        for r in (
            await store._session.execute(
                select(MemoryFact).where(MemoryFact.tenant_id == store._principal.tenant_id)
            )
        )
        .scalars()
        .all()
    }
    assert rows["my_company_name"].stale_after > rows["my_signatory"].stale_after


# -------------------------------------------------------------------- conflict


async def test_a_differing_value_is_a_conflict_not_an_update(store: MemoryStore) -> None:
    """India in March, Singapore in August. The store cannot know whether that is a changed
    preference or a one-off deal, so it refuses to guess."""
    await store.remember("preferred_governing_law_country", "India")

    outcome = await store.remember("preferred_governing_law_country", "Singapore")

    assert isinstance(outcome, Conflict)
    assert outcome.existing == "India"
    assert outcome.proposed == "Singapore"

    still = (await store.recall(["preferred_governing_law_country"]))[0]
    assert still.value == "India", "a conflict writes nothing"


async def test_the_same_value_again_is_not_a_conflict(store: MemoryStore) -> None:
    await store.remember("my_company_name", "ABC Pvt Ltd")
    outcome = await store.remember("my_company_name", "ABC Pvt Ltd")

    assert isinstance(outcome, Stored)


async def test_supersede_resolves_a_conflict_and_keeps_the_old_row(store: MemoryStore) -> None:
    await store.remember("preferred_governing_law_country", "India")
    result = await store.supersede("preferred_governing_law_country", "Singapore")

    assert result.superseded == "India"
    assert (await store.recall(["preferred_governing_law_country"]))[0].value == "Singapore"

    rows = (
        (
            await store._session.execute(
                select(MemoryFact).where(
                    MemoryFact.tenant_id == store._principal.tenant_id,
                    MemoryFact.key == "preferred_governing_law_country",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2, "append-only: the old belief survives"

    old = next(r for r in rows if r.value == "India")
    new = next(r for r in rows if r.value == "Singapore")
    assert old.superseded_by == new.id


# -------------------------------------------------------------------- forgetting


async def test_forget_tombstones_the_fact_but_keeps_the_row(store: MemoryStore) -> None:
    await store.remember("my_signatory", "Jane Rao")

    assert await store.forget("my_signatory") is True
    assert await store.recall(["my_signatory"]) == []

    rows = (
        (
            await store._session.execute(
                select(MemoryFact).where(MemoryFact.tenant_id == store._principal.tenant_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1 and rows[0].forgotten_at is not None


async def test_forgetting_then_remembering_again_works(store: MemoryStore) -> None:
    """The live-fact unique index excludes tombstoned rows, or this would violate it."""
    await store.remember("my_signatory", "Jane Rao")
    await store.forget("my_signatory")
    await store.remember("my_signatory", "Sam Patel")

    assert (await store.recall(["my_signatory"]))[0].value == "Sam Patel"


async def test_forget_all(store: MemoryStore) -> None:
    await store.remember("my_company_name", "ABC Pvt Ltd")
    await store.remember("my_signatory", "Jane Rao")

    assert await store.forget_all() == 2
    assert await store.all_facts() == []


# -------------------------------------------------------------------- scoping


async def test_one_user_cannot_recall_anothers_facts(store: MemoryStore) -> None:
    await store.remember("my_company_name", "ABC Pvt Ltd")

    other = MemoryStore(store._session, Principal(tenant_id=uuid.uuid4(), user_id=uuid.uuid4()))
    assert await other.recall(["my_company_name"]) == []
    assert await other.all_facts() == []


async def test_the_same_user_in_another_tenant_is_another_user(store: MemoryStore) -> None:
    await store.remember("my_company_name", "ABC Pvt Ltd")

    same_user_other_tenant = MemoryStore(
        store._session,
        Principal(tenant_id=uuid.uuid4(), user_id=store._principal.user_id),
    )
    assert await same_user_other_tenant.recall(["my_company_name"]) == []


def test_principal_is_constructed_in_one_place() -> None:
    """Auth lands in `core/principal.py` and nowhere else. If another module starts building
    Principals, the seam has leaked and spec 04 has more surface than it thinks."""
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "backend"
    builders: list[str] = []

    for path in root.rglob("*.py"):
        if path.name == "principal.py":
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Principal"
            ):
                builders.append(str(path.relative_to(root)))

    assert not builders, f"Principal constructed outside core/principal.py: {builders}"


def test_current_principal_is_stable_across_calls() -> None:
    """A fact remembered yesterday must still be yours today."""
    assert current_principal() == current_principal()
