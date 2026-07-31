"""Turning structure into meaning: three agents, one parse, in parallel.

The Template Provider produces structure — block ids, styles, numbering, text. This module
produces meaning, and does it with three separate agents:

- **Understanding** — what each section *is*. Reasoning.
- **Metadata** — the flat facts. Cheap extraction, and the only thing contract review needs.
- **Classification** — each clause described against the shared taxonomy. The most reused
  output the system produces.

## Why three agents and not one

They have different consumers, different failure modes, and different appropriate models.
Contract review wants metadata and nothing else; making it wait on full semantic
understanding would be wasteful for the commonest read. Classification is the one whose
output every future capability depends on, and it deserves the strongest model; metadata
extraction does not.

Splitting them also means a failure is partial. One agent returning nonsense costs its own
field rather than the whole understanding stage.

## Why one parse, dispatched in parallel

All three read the *same* `TemplateObject`. Parsing once and fanning out is a correctness
requirement, not an optimisation: three sequential passes would triple latency for no gain,
and — worse — three independent parses could disagree about block ids, at which point the
three artifacts describe subtly different documents and nothing downstream can join them.

`test_parallel_fanout.py` asserts `parse_docx` is called exactly once.

## Why the agents do not write their own artifacts

They return their objects; this module persists them, sequentially, after the fan-out
completes. Workspace writes take a per-contract advisory lock, so three agents writing
concurrently would serialise at best and deadlock against a caller-held transaction at
worst. Concurrency belongs on the model calls, which are the slow part; the writes are
fast and are better done in a known order.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.artifacts import Artifact, ArtifactStore
from backend.core.config import settings
from backend.core.logging import get_logger
from backend.core.prompts import load_prompt
from backend.core.run_context import RunContext
from backend.invariants.taxonomy import taxonomy_prompt_block, unknown_categories
from backend.runtime.adapters.openai_agents import runtime
from backend.runtime.port import AgentRuntime
from backend.runtime.spec import AgentSpec
from backend.schemas.cko import (
    ClauseCandidateSet,
    ContractMetadata,
    SemanticStructure,
)
from backend.schemas.template import TemplateObject

__all__ = [
    "RUNTIME",
    "Understanding",
    "build_classification_spec",
    "build_metadata_spec",
    "build_understanding_spec",
    "understand",
]

log = get_logger(__name__)

#: Replaced in tests. One seam for every spec-driven agent.
RUNTIME: AgentRuntime = runtime


@dataclass(frozen=True)
class Understanding:
    """What the three agents concluded, before any of it is persisted."""

    structure: SemanticStructure
    metadata: ContractMetadata
    clauses: ClauseCandidateSet


def build_understanding_spec() -> AgentSpec[SemanticStructure]:
    return AgentSpec(
        name="understanding_agent",
        prompt=load_prompt("understanding"),
        model=settings.understanding_model,
        output_model=SemanticStructure,
        max_turns=2,
        temperature=0.0,
    )


def build_metadata_spec() -> AgentSpec[ContractMetadata]:
    """Deliberately the cheapest of the three.

    Extraction, not judgement — and the field most often read on its own.
    """
    return AgentSpec(
        name="metadata_agent",
        prompt=load_prompt("metadata"),
        model=settings.metadata_model,
        output_model=ContractMetadata,
        max_turns=2,
        temperature=0.0,
    )


def build_classification_spec() -> AgentSpec[ClauseCandidateSet]:
    """The taxonomy is injected from the file rather than written into the prompt.

    One source of truth for the vocabulary. If the list a model is shown and the list it is
    validated against could drift apart, the validation would start failing for reasons
    nobody could see in the prompt.
    """
    return AgentSpec(
        name="classification_agent",
        prompt=load_prompt("classification").replace("{taxonomy}", taxonomy_prompt_block()),
        model=settings.classification_model,
        output_model=ClauseCandidateSet,
        max_turns=2,
        temperature=0.0,
    )


def _document_view(template: TemplateObject, texts: dict[str, str]) -> str:
    """The one rendering all three agents receive.

    Identical input by construction — the three specs are handed this exact string. Three
    agents given three slightly different views of a document would produce three artifacts
    that cannot be joined on block id, which is the failure the single-parse rule exists to
    prevent.

    Each block is prefixed with its id so the agents can reference blocks in their output.
    """
    lines = [f"# {template.filename}", ""]
    lines.extend(f"[{identity}] {text}".rstrip() for identity, text in texts.items())
    return "\n".join(lines)


async def understand(
    template: TemplateObject,
    texts: dict[str, str],
    ctx: RunContext,
) -> Understanding:
    """Run all three agents over one document view, then persist their artifacts.

    `texts` is `block_id -> text`, produced once by the caller from a single parse.
    """
    view = _document_view(template, texts)

    structure_spec = build_understanding_spec()
    metadata_spec = build_metadata_spec()
    classification_spec = build_classification_spec()

    results = await RUNTIME.run_many(
        [
            (structure_spec, view),
            (metadata_spec, view),
            (classification_spec, view),
        ],
        ctx,
    )

    structure = results[0].output
    metadata = results[1].output
    clauses = results[2].output
    assert structure is not None and metadata is not None and clauses is not None

    unknown = unknown_categories(clauses.candidates)
    if unknown:
        # Recorded, not raised. A near-miss category is a classification defect, and the
        # place to block on it is validation — where a human sees the finding — rather
        # than here, where it would discard two perfectly good artifacts as well.
        log.warning("classification used categories outside the taxonomy: %s", list(unknown))

    # Sequential writes, after the fan-out. See the module docstring.
    artifacts = ArtifactStore(ctx.session_factory, ctx.contract_id)
    await artifacts.save(Artifact.UNDERSTANDING, structure)
    await artifacts.save(Artifact.METADATA, metadata)
    await artifacts.save(Artifact.CLAUSE_CANDIDATES, clauses)

    return Understanding(structure=structure, metadata=metadata, clauses=clauses)
