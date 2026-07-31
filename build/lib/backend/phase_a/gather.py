"""Running the resolved providers.

This module is the one the extensibility claim is about. It names no provider, imports no
provider, and knows nothing about templates, clause libraries or playbooks. It asks the
registry who is available, runs them, and returns what they said.

If adding a knowledge source ever requires editing this file, the provider pattern has
failed and `tests/test_provider_extensibility.py` should be the thing that says so.

Providers run **concurrently**. They are independent by contract — none may read another's
output — so there is no ordering requirement between them, and reference-document analysis
in particular is slow enough that running three sequentially is a user-visible delay.
Ordering is applied to the *results*, by precedence, so the aggregator always sees the same
sequence regardless of who finished first.
"""

from __future__ import annotations

import asyncio

from backend.core.logging import get_logger
from backend.core.run_context import RunContext
from backend.knowledge.base import precedence_of
from backend.knowledge.registry import get_provider
from backend.schemas.intent import IntentObject, ResolutionPlan
from backend.schemas.provider import KnowledgeContribution

__all__ = ["gather"]

log = get_logger(__name__)


async def gather(
    plan: ResolutionPlan,
    intent: IntentObject,
    ctx: RunContext,
) -> tuple[KnowledgeContribution, ...]:
    """Run every provider in `plan` and return their contributions, highest authority first.

    A provider that fails does not fail the run. Losing the reference-document analysis
    should degrade the result, not destroy it — the aggregator records what it received and
    the confidence report shows what is missing. A provider that fails *silently* would be
    worse than one that fails loudly, so the failure is logged and surfaced through the
    absent contribution rather than swallowed.
    """
    providers = [get_provider(name) for name in plan.providers]
    results = await asyncio.gather(
        *(provider.contribute(intent, ctx) for provider in providers),
        return_exceptions=True,
    )

    contributions: list[KnowledgeContribution] = []
    for provider, result in zip(providers, results, strict=True):
        if isinstance(result, BaseException):
            log.warning("provider=%s failed: %s", provider.name, type(result).__name__)
            continue
        contributions.append(result)

    return tuple(sorted(contributions, key=lambda c: (precedence_of(c.provider), c.provider)))
