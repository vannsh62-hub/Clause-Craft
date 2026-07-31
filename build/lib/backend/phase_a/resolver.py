"""Deciding which knowledge sources participate, once, up front.

Every downstream stage reads the `ResolutionPlan`. Nothing downstream asks "was a template
uploaded?" — that question has exactly one answer per run, and it is answered here and
recorded in an artifact.

## Why this is not an agent

Spec 05 §6.2 describes the Resolver as a sub-agent returning a `ResolutionPlan`. It is
implemented deterministically instead, and the departure is deliberate.

"Which providers participate" is already answered by `available()` on each provider: is a
template uploaded, does this tenant have a playbook, is there a clause library for this
contract type. Those are lookups with right answers. Putting a model in front of them buys
nothing and adds a failure mode — a resolver that hallucinates a provider produces a run
that fails at `get_provider`, and one that omits a real provider silently drops a
compliance rule.

There *is* a genuine judgement in this area, and it is a consequential one: whether an
uploaded document is a **template** to convert or a **reference** to learn from. Those
paths are opposites — template text is authoritative and must be preserved, reference text
must never be copied (§7) — and no `available()` check can decide it. That judgement
belongs with the providers that implement those paths, and lands with them at M5 and M10.
Until then there is nothing here for a model to decide.

## The invariant

A plan may **narrow** the default precedence and may never **reorder** it. Narrowing is a
fact about this run: no template was uploaded, so the template provider does not
participate. Reordering would be a policy change — and a resolver able to make policy
changes could demote the playbook below the template, which is how a compliance rule stops
applying without anyone deciding that it should.

`validate_plan` enforces this rather than trusting it, because the failure is invisible:
the run succeeds, the contract is produced, and the only symptom is that the wrong source
won a conflict nobody was told about.
"""

from __future__ import annotations

from backend.artifacts import Artifact, ArtifactStore
from backend.core.run_context import RunContext
from backend.knowledge.base import order_by_precedence, precedence_of
from backend.knowledge.registry import available_providers
from backend.schemas.errors import ContractToolError
from backend.schemas.intent import IntentObject, ResolutionPlan

__all__ = ["ResolutionError", "plan_for", "resolve", "validate_plan"]


class ResolutionError(ContractToolError):
    """A resolution plan that contradicts the precedence policy."""


def validate_plan(plan: ResolutionPlan) -> None:
    """Raise unless `plan` merely narrows the fixed precedence order.

    Checks the *relative* order of the providers present, so a plan that omits sources is
    fine and a plan that swaps two is not.
    """
    ranks = [precedence_of(name) for name in plan.providers]
    if ranks != sorted(ranks):
        correct = list(order_by_precedence(plan.providers))
        raise ResolutionError(
            f"resolution plan {list(plan.providers)} reorders the precedence policy; "
            f"the correct order for these providers is {correct}. "
            "A plan may narrow precedence — a source that has nothing to offer does not "
            "participate — but may not reorder it, because precedence decides which source "
            "wins a conflict and that is policy, not a per-run judgement."
        )

    duplicates = {name for name in plan.providers if plan.providers.count(name) > 1}
    if duplicates:
        raise ResolutionError(
            f"resolution plan lists {sorted(duplicates)} more than once; a provider that "
            "contributes twice would silently outweigh its own precedence"
        )


async def plan_for(intent: IntentObject, ctx: RunContext) -> ResolutionPlan:
    """Build and validate the plan, without persisting it.

    Separate from `resolve` so the decision can be inspected — in a test, or in a dry run —
    without a database write. Availability narrows the set; precedence orders it.
    """
    names = tuple(p.name for p in await available_providers(intent, ctx))
    plan = ResolutionPlan(
        providers=order_by_precedence(names),
        rationale=_rationale(intent, names),
    )
    validate_plan(plan)
    return plan


async def resolve(intent: IntentObject, ctx: RunContext) -> ResolutionPlan:
    """Decide who participates, and record the decision."""
    plan = await plan_for(intent, ctx)
    await ArtifactStore(ctx.session_factory, ctx.contract_id).save(Artifact.RESOLUTION, plan)
    return plan


def _rationale(intent: IntentObject, names: tuple[str, ...]) -> str:
    """Why this set, in a form a human can check against the artifact."""
    if not names:
        return "no knowledge provider is available for this request"
    return (
        f"{len(names)} provider(s) available for a {intent.contract_type} in "
        f"{intent.mode} mode: {', '.join(names)}"
    )
