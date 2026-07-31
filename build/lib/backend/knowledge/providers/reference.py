"""Reference documents as a knowledge source — analysed, never copied.

The most commonly misimplemented part of the system, so it is built to make the mistake
structurally impossible rather than merely discouraged.

A reference document is somebody else's contract, provided so the drafter can learn the
shape of this kind of agreement. Its text is not authoritative and must never reach the
output. This is enforced in three overlapping ways:

1. **The analyzer returns a `KnowledgeGraph`, which has no field that can hold clause
   text.** Categories, short obligation summaries, term meanings, patterns — no verbatim
   slot. The drafter physically cannot copy what it is never given.
2. **Only the graph enters the CKO.** The reference *text* stays in the workspace and never
   crosses into Phase B. The drafting agent sees the graph, not the document.
3. **A leakage gate at validation** (M2's `invariants/leakage.py`) catches any distinctive
   run that slips through anyway — the belt to the structural braces.

Contrast with the template provider: a template's text *is* authoritative and is preserved.
The two are kept in separate providers, separate workspace prefixes, and separate object
types precisely so they can never be confused at a call site (§7).

## Parallel

One analyzer invocation per document, run concurrently. Reference analysis is slow, and the
documents are independent — nothing about one informs another — so there is no reason to run
them in series. Results come back in filename order regardless of which finished first.
"""

from __future__ import annotations

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.core.prompts import load_prompt
from backend.core.run_context import RunContext
from backend.knowledge.registry import register_provider
from backend.runtime.adapters.openai_agents import runtime
from backend.runtime.port import AgentRuntime
from backend.runtime.spec import AgentSpec
from backend.schemas.cko import KnowledgeGraph, Provenance
from backend.schemas.intent import IntentObject
from backend.schemas.provider import KnowledgeContribution
from backend.workspace.store import REFERENCE_PREFIX, WorkspaceStore

__all__ = ["RUNTIME", "ReferenceProvider", "build_reference_spec"]

log = get_logger(__name__)

#: Replaced in tests. One seam for every spec-driven agent.
RUNTIME: AgentRuntime = runtime


def build_reference_spec() -> AgentSpec[KnowledgeGraph]:
    """The reference analyzer, as data.

    Structured output whose type has no verbatim-text field — the structural guarantee is
    in the schema, not in the prompt. Temperature 0: the same document should yield the same
    knowledge.
    """
    return AgentSpec(
        name="reference_analyzer",
        prompt=load_prompt("reference"),
        model=settings.reference_model,
        output_model=KnowledgeGraph,
        max_turns=2,
        temperature=0.0,
    )


class ReferenceProvider:
    """Knowledge extracted from uploaded reference documents."""

    name = "reference"

    async def available(self, intent: IntentObject, ctx: RunContext) -> bool:
        """Any reference document was uploaded for this run? A workspace listing."""
        return bool(await self._reference_paths(ctx))

    async def contribute(self, intent: IntentObject, ctx: RunContext) -> KnowledgeContribution:
        paths = await self._reference_paths(ctx)
        graphs = await self._analyze(paths, ctx)
        return KnowledgeContribution(
            provider=self.name,
            provenance=Provenance(
                provider=self.name,
                locator=f"{len(graphs)} reference document(s)",
            ),
            reference_knowledge=tuple(graphs),
        )

    async def _reference_paths(self, ctx: RunContext) -> list[str]:
        async with ctx.session_factory() as session:
            files = await WorkspaceStore(session).ls(ctx.contract_id)
        return sorted(f.path for f in files if f.path.startswith(REFERENCE_PREFIX))

    async def _analyze(self, paths: list[str], ctx: RunContext) -> list[KnowledgeGraph]:
        """One analyzer per document, in parallel. Returns graphs in `paths` order.

        A document that fails to analyze is skipped with a warning rather than failing the
        run — losing one reference should degrade the result, not destroy it. The gather
        step already treats a failing provider that way; here the same principle applies
        per document.
        """
        texts = await self._read(paths, ctx)
        jobs = [(build_reference_spec(), _instruction(path, text)) for path, text in texts]
        results = await RUNTIME.run_many(jobs, ctx)

        graphs: list[KnowledgeGraph] = []
        for (path, _), result in zip(texts, results, strict=True):
            if result.output is None:  # pragma: no cover - the port validates output
                log.warning("reference analysis produced no graph for %s", path)
                continue
            graphs.append(result.output)
        return graphs

    async def _read(self, paths: list[str], ctx: RunContext) -> list[tuple[str, str]]:
        async with ctx.session_factory() as session:
            store = WorkspaceStore(session)
            return [(path, await store.read(ctx.contract_id, path)) for path in paths]


def _instruction(path: str, text: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return (
        f"Analyse this reference document ({name}) and report what it teaches — never its "
        "wording.\n\n"
        f"{text}"
    )


register_provider(ReferenceProvider())
