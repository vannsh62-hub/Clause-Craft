"""Reading and writing typed artifacts.

The only writer of the paths in `names.py`. Going through one place buys three things that
matter more than the small indirection:

- **A missing artifact is an exception, not an empty object.** `load` raising
  `ArtifactMissing` is the mechanism the transformation precondition uses: the drafting
  sub-agent is never constructed if its plan is absent, so a missing plan costs nothing
  rather than costing a model call that then fails.
- **Types cannot drift from paths.** The `Artifact` enum binds them, so nothing can write
  a `DraftPlan` to the CKO's path.
- **Persisted JSON is stable.** A fixed indent, plus pydantic's field-declaration ordering,
  means an artifact diff shows what changed rather than how the serialiser felt.

Storage is `WorkspaceStore`, the Postgres-backed virtual filesystem, so artifacts inherit
its path canonicalisation, its per-contract advisory lock, and its read-only prefixes
without re-implementing any of them.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.artifacts.names import Artifact
from backend.schemas.errors import ContractToolError
from backend.workspace.store import WorkspaceStore

__all__ = ["ArtifactMissing", "ArtifactStore", "MalformedArtifact"]


class ArtifactMissing(ContractToolError):
    """A required artifact has not been produced yet.

    A `ContractToolError` so the orchestrator can decide what to do — usually produce the
    missing artifact — rather than the run dying. When it is raised as a *precondition*,
    the point is that it happens before any model is invoked.
    """


class MalformedArtifact(ContractToolError):
    """An artifact exists but does not parse as its declared type.

    Distinct from `ArtifactMissing` on purpose: missing means "do the step", malformed
    means "the step produced garbage" and re-running it blindly will probably do so again.
    """


class ArtifactStore:
    """Typed access to one contract's artifacts."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        contract_id: uuid.UUID,
    ) -> None:
        self._session_factory = session_factory
        self._contract_id = contract_id

    async def save(self, artifact: Artifact, value: BaseModel) -> None:
        """Persist `value` at `artifact`'s canonical path.

        Refuses a mismatched type rather than serialising it: writing a `DraftPlan` to the
        CKO's path would fail later, at read time, in a different stack, with no clue as
        to who wrote it.
        """
        if not isinstance(value, artifact.model):
            raise MalformedArtifact(
                f"{artifact.name} holds {artifact.model.__name__}, but got {type(value).__name__}."
            )
        payload = value.model_dump_json(indent=2)
        async with self._session_factory() as session:
            await WorkspaceStore(session).write(self._contract_id, artifact.path, payload)
            await session.commit()

    async def load(self, artifact: Artifact) -> BaseModel:
        """Read and validate `artifact`.

        Raises `ArtifactMissing` if it has not been written, `MalformedArtifact` if it
        does not parse.
        """
        async with self._session_factory() as session:
            store = WorkspaceStore(session)
            if not await store.exists(self._contract_id, artifact.path):
                raise ArtifactMissing(
                    f"{artifact.path} has not been produced yet. "
                    f"Run the step that writes it before reading it."
                )
            raw = await store.read(self._contract_id, artifact.path)

        try:
            return artifact.model.model_validate_json(raw)
        except ValidationError as exc:
            raise MalformedArtifact(
                f"{artifact.path} exists but is not a valid {artifact.model.__name__}."
            ) from exc

    async def exists(self, artifact: Artifact) -> bool:
        async with self._session_factory() as session:
            return await WorkspaceStore(session).exists(self._contract_id, artifact.path)

    async def require(self, artifact: Artifact) -> BaseModel:
        """`load`, named for use as a precondition.

        Call this *before* constructing whatever needs the artifact. The distinction is
        the whole of milestone M8: a precondition that runs after the sub-agent is built
        has already spent tokens deciding it cannot proceed.
        """
        return await self.load(artifact)
