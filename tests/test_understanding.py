"""Three agents, one parse, in parallel.

The load-bearing property of M6 is that Understanding, Metadata and Classification all read
the *same* parsed document. Three sequential passes would triple latency; three independent
parses could disagree about block ids, at which point the three artifacts describe subtly
different documents and nothing downstream can join them. So the parse happens once and the
agents fan out over it.

A single scripted `FakeModel` cannot drive this: the three agents want three different
output shapes, and under `asyncio.gather` the order in which they call the model is not
fixed. The fake here routes by which agent is calling — matched on its system prompt — so
it is correct regardless of scheduling.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.artifacts import Artifact, ArtifactStore
from backend.core.config import settings
from backend.core.run_context import RunContext
from backend.phase_a import understanding as understanding_mod
from backend.runtime.adapters.openai_agents import OpenAIAgentsRuntime
from backend.schemas.cko import ClauseCandidateSet, ContractMetadata, SemanticStructure
from backend.workspace.models import Contract
from tests.fakes import Capture, FakeModel, Turn, text_message

TEXTS = {
    "b1": "Service Level Agreement",
    "b2": "Uptime shall be 99.9%, measured monthly.",
    "b3": "Either party may terminate on 30 days notice.",
    "b4": "This Agreement is governed by the laws of India.",
}

STRUCTURE = {
    "sections": [
        {"block_id": "b3", "role": "termination", "summary": "30 days notice, either party"},
        {"block_id": "b4", "role": "governing_law", "summary": "India"},
    ],
    "definitions": [],
    "unclassified": [],
    "confidence": 0.9,
}
METADATA = {"jurisdiction": "IN", "governing_law": "India", "payment_terms_days": None}
CLAUSES = {
    "candidates": [
        {"category": "termination", "source_ref": {"provider": "template", "block_id": "b3"}},
        {"category": "governing_law", "source_ref": {"provider": "template", "block_id": "b4"}},
    ]
}

SSTRUCTURE_OBJ = SemanticStructure.model_validate(STRUCTURE)
METADATA_OBJ = ContractMetadata.model_validate(METADATA)
CLAUSES_OBJ = ClauseCandidateSet.model_validate(CLAUSES)

#: The real runtime, captured so the concurrency test can restore it after swapping in a
#: deliberately slow one.
runtime_default = understanding_mod.RUNTIME


class RoutingFakeModel(FakeModel):
    """Answers each of the three understanding agents with its own payload.

    Routes on the system prompt because that is what distinguishes the agents and is stable
    under any gather ordering. Still records every call, so the fan-out can be inspected.
    """

    def __init__(self) -> None:
        super().__init__([])
        self.by_agent: dict[str, int] = {}

    async def get_response(self, system_instructions: str | None, *args: Any, **kwargs: Any) -> Any:
        prompt = system_instructions or ""
        if "Contract understanding" in prompt:
            agent, payload = "understanding", STRUCTURE
        elif "Metadata extraction" in prompt:
            agent, payload = "metadata", METADATA
        elif "Clause classification" in prompt:
            agent, payload = "classification", CLAUSES
        else:  # pragma: no cover - a fourth agent would be a bug
            raise AssertionError(f"unexpected agent prompt: {prompt[:40]!r}")

        self.by_agent[agent] = self.by_agent.get(agent, 0) + 1
        self._turns = [Turn(output=[text_message(json.dumps(payload))])]
        return await super().get_response(system_instructions, *args, **kwargs)


@pytest_asyncio.fixture
async def ctx() -> AsyncIterator[RunContext]:
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    cid = uuid.uuid4()
    async with factory() as s:
        s.add(Contract(id=cid, contract_type="sla", request="Convert this SLA"))
        await s.commit()
    try:
        yield RunContext(contract_id=cid, session_factory=factory, contract_type="sla")
    finally:
        async with factory() as s:
            await s.execute(delete(Contract).where(Contract.id == cid))
            await s.commit()
        await engine.dispose()


def _template() -> Any:
    from backend.schemas.template import BlockFingerprint, FormattingManifest, TemplateObject

    return TemplateObject(
        storage_key="k.docx",
        sha256="a" * 64,
        size_bytes=1,
        filename="sla.docx",
        formatting=FormattingManifest(
            blocks=tuple(
                BlockFingerprint(index=i, kind="paragraph", text_sha=b) for i, b in enumerate(TEXTS)
            )
        ),
    )


def _use(monkeypatch: pytest.MonkeyPatch, model: FakeModel) -> None:
    monkeypatch.setattr(understanding_mod, "RUNTIME", OpenAIAgentsRuntime(model))


def _input_text(capture: Capture) -> str:
    """The user input a capture saw, flattened — excluding the system prompt."""
    if isinstance(capture.input, str):
        return capture.input
    return json.dumps(capture.input, default=str)


# ----------------------------------------------------------------- the fan-out property


async def test_all_three_artifacts_are_produced(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, RoutingFakeModel())

    result = await understanding_mod.understand(_template(), TEXTS, ctx)

    assert isinstance(result.structure, SemanticStructure)
    assert isinstance(result.metadata, ContractMetadata)
    assert isinstance(result.clauses, ClauseCandidateSet)
    assert result.metadata.jurisdiction == "IN"
    assert {c.category for c in result.clauses.candidates} == {"termination", "governing_law"}


async def test_each_agent_is_invoked_exactly_once(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = RoutingFakeModel()
    _use(monkeypatch, fake)

    await understanding_mod.understand(_template(), TEXTS, ctx)

    assert fake.by_agent == {"understanding": 1, "metadata": 1, "classification": 1}


async def test_the_document_is_parsed_once_not_once_per_agent(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reason the parse happens in the caller, not in each agent.

    `understand` receives an already-parsed template and a text map. It must not re-parse:
    three parses could assign different block ids and the artifacts would stop joining.
    Here that is enforced by construction — there is no document bytes to parse — and the
    assertion is that all three agents saw the *same* block ids.
    """
    fake = RoutingFakeModel()
    _use(monkeypatch, fake)

    await understanding_mod.understand(_template(), TEXTS, ctx)

    # The *input* only — not `as_text()`, which folds in the system prompt, and the three
    # prompts differ by design. What must be identical is the document each agent read.
    views = {_input_text(c) for c in fake.captures}
    assert len(views) == 1, "all three agents must see one identical document view"
    assert "[b3]" in next(iter(views)), "blocks are addressed by their parse-time ids"


async def test_the_fan_out_is_actually_concurrent(ctx: RunContext) -> None:
    """Timing evidence, not just structural.

    The single-parse rule prevents artifacts that cannot be joined; running the agents
    concurrently is the reason the rule costs nothing. Three agents that each take a fixed
    delay must finish in roughly one delay, not three — otherwise the fan-out has silently
    become a sequence and Phase A's latency triples.
    """
    import asyncio
    import time

    from backend.runtime.spec import AgentResult

    class SlowRuntime:
        name = "slow"

        async def run(self, spec: Any, c: Any, instruction: str, *, history: Any = None) -> Any:
            await asyncio.sleep(0.2)
            payloads = {
                "understanding_agent": SSTRUCTURE_OBJ,
                "metadata_agent": METADATA_OBJ,
                "classification_agent": CLAUSES_OBJ,
            }
            return AgentResult(text="{}", output=payloads[spec.name])

        async def run_many(self, jobs: Any, c: Any) -> Any:
            return list(await asyncio.gather(*(self.run(s, c, t) for s, t in jobs)))

    understanding_mod.RUNTIME = SlowRuntime()  # type: ignore[assignment]
    try:
        start = time.monotonic()
        await understanding_mod.understand(_template(), TEXTS, ctx)
        elapsed = time.monotonic() - start
    finally:
        understanding_mod.RUNTIME = runtime_default

    assert elapsed < 0.5, f"three 0.2s agents took {elapsed:.2f}s — the fan-out serialised"


async def test_all_three_artifacts_are_persisted(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, RoutingFakeModel())
    await understanding_mod.understand(_template(), TEXTS, ctx)

    artifacts = ArtifactStore(ctx.session_factory, ctx.contract_id)
    assert isinstance(await artifacts.load(Artifact.UNDERSTANDING), SemanticStructure)
    assert isinstance(await artifacts.load(Artifact.METADATA), ContractMetadata)
    assert isinstance(await artifacts.load(Artifact.CLAUSE_CANDIDATES), ClauseCandidateSet)


# --------------------------------------------------------------------- taxonomy conformance


async def test_a_category_outside_the_taxonomy_is_recorded_not_fatal(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A near-miss category is a classification defect, and validation is where it blocks.

    Raising here would discard the other two perfectly good artifacts along with it.
    """

    class BadCategory(RoutingFakeModel):
        async def get_response(self, system_instructions: str | None, *a: Any, **k: Any) -> Any:
            if "Clause classification" in (system_instructions or ""):
                self._turns = [
                    Turn(
                        output=[
                            text_message(
                                json.dumps(
                                    {"candidates": [{"category": "confidential_information"}]}
                                )
                            )
                        ]
                    )
                ]
                self.by_agent["classification"] = 1
                return await FakeModel.get_response(self, system_instructions, *a, **k)
            return await super().get_response(system_instructions, *a, **k)

    _use(monkeypatch, BadCategory())

    result = await understanding_mod.understand(_template(), TEXTS, ctx)

    # It is preserved as written — the CKO records what the agent said — and the other
    # artifacts landed.
    assert result.clauses.candidates[0].category == "confidential_information"
    assert result.metadata.jurisdiction == "IN"


# ------------------------------------------------------------------------- agent specs


def test_the_agents_are_configured_for_their_jobs() -> None:
    understanding = understanding_mod.build_understanding_spec()
    metadata = understanding_mod.build_metadata_spec()
    classification = understanding_mod.build_classification_spec()

    assert understanding.output_model is SemanticStructure
    assert metadata.output_model is ContractMetadata
    assert classification.output_model is ClauseCandidateSet
    for spec in (understanding, metadata, classification):
        assert spec.temperature == 0.0
        assert spec.tools == ()


def test_the_classification_prompt_carries_the_taxonomy() -> None:
    """Injected from the file, so the list shown and the list validated cannot diverge."""
    prompt = understanding_mod.build_classification_spec().prompt
    assert "{taxonomy}" not in prompt, "the placeholder must be filled"
    assert "`termination`" in prompt and "`indemnity`" in prompt
