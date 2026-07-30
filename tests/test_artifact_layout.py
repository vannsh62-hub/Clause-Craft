"""The artifact layout, and the store that is the only thing allowed to write it.

The layout is the explainability feature: "why was the arbitration clause removed?" is a
file read, not a re-run. That only holds if the paths are stable, canonical, and written by
one code path — so those three things are asserted here rather than assumed.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.artifacts import Artifact, ArtifactMissing, ArtifactStore
from backend.artifacts.store import MalformedArtifact
from backend.core.config import settings
from backend.schemas.cko import ContractKnowledgeObject
from backend.schemas.intent import IntentObject, ResolutionPlan
from backend.schemas.plan import DraftPlan, PlannedSection, SectionDecision, TransformationPlan
from backend.workspace.models import Contract
from backend.workspace.store import WorkspaceStore, _canonical


@pytest_asyncio.fixture
async def store() -> AsyncIterator[
    tuple[ArtifactStore, uuid.UUID, async_sessionmaker[AsyncSession]]
]:
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    cid = uuid.uuid4()
    async with factory() as s:
        s.add(Contract(id=cid, contract_type="nda", request="Draft an NDA"))
        await s.commit()
    try:
        yield ArtifactStore(factory, cid), cid, factory
    finally:
        async with factory() as s:
            await s.execute(delete(Contract).where(Contract.id == cid))
            await s.commit()
        await engine.dispose()


def _intent() -> IntentObject:
    return IntentObject(contract_type="nda", confidence=0.9, purpose="protect disclosures")


def _cko(contract_id: uuid.UUID) -> ContractKnowledgeObject:
    return ContractKnowledgeObject(
        contract_id=contract_id,
        resolution=ResolutionPlan(providers=("llm",)),
        intent=_intent(),
    )


# ---------------------------------------------------------------------------- layout


def test_every_artifact_path_is_canonical() -> None:
    """A path the workspace would reject is a runtime failure waiting for a feature flag.

    `_canonical` refuses uppercase, `..`, empty segments and leading slashes. Checking at
    import time means a mistyped path fails in CI rather than in the middle of a run.
    """
    for artifact in Artifact:
        assert _canonical(artifact.path) == artifact.path, artifact.name


def test_paths_are_unique() -> None:
    paths = [a.path for a in Artifact]
    assert len(paths) == len(set(paths))


def test_the_layout_matches_the_spec() -> None:
    """Pinned so a rename is a deliberate act.

    These filenames appear in the spec, in support answers, and eventually in a UI. The
    numeric prefixes carry the pipeline order, and the jump from 04 to 05 is the phase
    boundary.
    """
    assert Artifact.INTENT.path == "work/00-intent.json"
    assert Artifact.RESOLUTION.path == "work/01-resolution-plan.json"
    assert Artifact.TEMPLATE.path == "work/02-template.json"
    assert Artifact.CKO.path == "work/04-cko.json"
    assert Artifact.DRAFT_PLAN.path == "work/05-draft-plan.json"
    assert Artifact.TRANSFORMATION_PLAN.path == "work/06-transformation-plan.json"


def test_artifacts_do_not_collide_with_the_read_only_prefixes() -> None:
    """`clauses/` and friends are read-only; an artifact routed there could never be written."""
    for artifact in Artifact:
        assert not artifact.path.startswith("clauses/"), artifact.name


# --------------------------------------------------------------------- round-tripping


async def test_each_artifact_round_trips(
    store: tuple[ArtifactStore, uuid.UUID, async_sessionmaker[AsyncSession]],
) -> None:
    artifacts, cid, _ = store
    values = {
        Artifact.INTENT: _intent(),
        Artifact.RESOLUTION: ResolutionPlan(providers=("playbook", "llm"), rationale="why"),
        Artifact.CKO: _cko(cid),
        Artifact.DRAFT_PLAN: DraftPlan(
            sections=(PlannedSection(name="Confidentiality", order=0, rationale="r", source="llm"),)
        ),
        Artifact.TRANSFORMATION_PLAN: TransformationPlan(
            keep=(SectionDecision(name="Confidentiality", decision="keep", reason="applies"),)
        ),
    }

    for artifact, value in values.items():
        await artifacts.save(artifact, value)
        assert await artifacts.load(artifact) == value


async def test_saving_writes_to_the_declared_path(
    store: tuple[ArtifactStore, uuid.UUID, async_sessionmaker[AsyncSession]],
) -> None:
    artifacts, cid, factory = store
    await artifacts.save(Artifact.INTENT, _intent())

    async with factory() as s:
        paths = [f.path for f in await WorkspaceStore(s).ls(cid)]
    assert Artifact.INTENT.path in paths


# --------------------------------------------------------------------------- refusals


async def test_a_missing_artifact_raises_rather_than_returning_an_empty_object(
    store: tuple[ArtifactStore, uuid.UUID, async_sessionmaker[AsyncSession]],
) -> None:
    """The mechanism the transformation precondition depends on (M8).

    An empty `TransformationPlan` would look like "keep nothing, change nothing" and the
    drafting agent would happily proceed to regenerate the document.
    """
    artifacts, _, _ = store
    with pytest.raises(ArtifactMissing, match="06-transformation-plan.json"):
        await artifacts.load(Artifact.TRANSFORMATION_PLAN)


async def test_exists_is_false_before_and_true_after(
    store: tuple[ArtifactStore, uuid.UUID, async_sessionmaker[AsyncSession]],
) -> None:
    artifacts, _, _ = store
    assert await artifacts.exists(Artifact.INTENT) is False
    await artifacts.save(Artifact.INTENT, _intent())
    assert await artifacts.exists(Artifact.INTENT) is True


async def test_writing_the_wrong_type_is_refused_at_the_write(
    store: tuple[ArtifactStore, uuid.UUID, async_sessionmaker[AsyncSession]],
) -> None:
    """Fail where the mistake was made, not later in an unrelated stack."""
    artifacts, _, _ = store
    with pytest.raises(MalformedArtifact, match="ContractKnowledgeObject"):
        await artifacts.save(Artifact.CKO, DraftPlan())


async def test_an_unparseable_artifact_is_distinguished_from_a_missing_one(
    store: tuple[ArtifactStore, uuid.UUID, async_sessionmaker[AsyncSession]],
) -> None:
    """Missing means "do the step". Malformed means re-running it will likely fail again."""
    artifacts, cid, factory = store
    async with factory() as s:
        await WorkspaceStore(s).write(cid, Artifact.CKO.path, '{"nonsense": true}')
        await s.commit()

    with pytest.raises(MalformedArtifact, match="not a valid ContractKnowledgeObject"):
        await artifacts.load(Artifact.CKO)


async def test_artifacts_are_scoped_to_one_contract(
    store: tuple[ArtifactStore, uuid.UUID, async_sessionmaker[AsyncSession]],
) -> None:
    """Two runs must not read each other's artifacts."""
    artifacts, _, factory = store
    other = uuid.uuid4()
    async with factory() as s:
        s.add(Contract(id=other, contract_type="nda", request="Another NDA"))
        await s.commit()
    try:
        await artifacts.save(Artifact.INTENT, _intent())
        assert await ArtifactStore(factory, other).exists(Artifact.INTENT) is False
    finally:
        async with factory() as s:
            await s.execute(delete(Contract).where(Contract.id == other))
            await s.commit()
