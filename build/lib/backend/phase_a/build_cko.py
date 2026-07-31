"""Assembling and persisting the Contract Knowledge Object.

The thin, I/O-bearing wrapper around `aggregate`, which is pure. Kept separate so the merge
logic can be tested as a table with no database in sight, and so `04-cko.json` — the phase
boundary artifact — has exactly one writer.
"""

from __future__ import annotations

from backend.artifacts import Artifact, ArtifactStore
from backend.core.run_context import RunContext
from backend.phase_a.aggregator import aggregate
from backend.schemas.cko import ContractKnowledgeObject
from backend.schemas.intent import IntentObject, ResolutionPlan
from backend.schemas.provider import KnowledgeContribution

__all__ = ["build_and_store_cko"]


async def build_and_store_cko(
    contributions: tuple[KnowledgeContribution, ...],
    intent: IntentObject,
    resolution: ResolutionPlan,
    ctx: RunContext,
) -> ContractKnowledgeObject:
    """Merge the contributions into a CKO and write `04-cko.json`.

    The CKO is the only thing that crosses into Phase B, so this is the last write of
    Phase A. `tenant_id` is threaded from the run context when it exists; today it does
    not, and that is recorded honestly as `None` rather than forged.
    """
    cko = aggregate(
        contributions,
        intent,
        resolution,
        contract_id=ctx.contract_id,
        tenant_id=getattr(ctx, "tenant_id", None),
    )
    await ArtifactStore(ctx.session_factory, ctx.contract_id).save(Artifact.CKO, cko)
    return cko
