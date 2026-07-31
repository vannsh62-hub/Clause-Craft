"""The knowledge provider contract.

Every source of knowledge — an uploaded template, the clause library, a reference
document, a playbook, the model's own knowledge — implements this one interface and returns
one `KnowledgeContribution`. Nothing downstream branches on which kind of source it is
talking to.

The alternative is what the codebase would otherwise grow: `if template: ... elif
clause_library: ...` threaded through the pipeline, so that adding a regulatory database or
a policy store means editing every stage that ever asks where knowledge came from. Under
this interface it means adding one file.

Two rules make it hold:

**Providers are independent.** No provider may read another's output. All cross-source
reconciliation happens once, in the aggregator, where precedence is explicit and conflicts
are recorded. A provider that peeked at another's contribution would make the outcome
depend on execution order, and precedence would silently become "whoever ran last".

**Precedence is policy, not judgement.** The order below is fixed. A run may *narrow* it —
no template was uploaded, so the template provider does not participate — but may never
reorder it. A pipeline that could reorder it could quietly demote the playbook, which is
how a compliance rule stops applying without anyone deciding that it should.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.core.run_context import RunContext
from backend.schemas.intent import IntentObject
from backend.schemas.provider import KnowledgeContribution

__all__ = ["PRECEDENCE", "KnowledgeProvider", "order_by_precedence", "precedence_of"]

#: Highest authority first. A playbook is policy and outranks everything; the model's own
#: knowledge is the floor and loses to any real source.
#:
#: Spec 05 §6.12. Changing this order changes which source wins a conflict, so it is a
#: policy decision that belongs in review, not a tunable.
PRECEDENCE: tuple[str, ...] = (
    "playbook",
    "clause_library",
    "template",
    "reference",
    "llm",
)


@runtime_checkable
class KnowledgeProvider(Protocol):
    """One source of contract knowledge."""

    #: Stable identifier, and the key used for precedence. Must appear in `PRECEDENCE`
    #: unless the provider is deliberately experimental, in which case it sorts last.
    name: str

    async def available(self, intent: IntentObject, ctx: RunContext) -> bool:
        """Whether this provider has anything to offer for this run.

        Async because the honest answers require I/O: *was a template uploaded*, *does this
        tenant have a playbook*, *is there a clause library for this contract type*. An
        earlier version of this protocol was synchronous on the theory that availability
        should be cheap — but that only moved the I/O into `contribute`, where a provider
        that turns out to have nothing still appears in the resolution plan and has to
        return an empty contribution. The plan is the record of what participated, so it
        should record what actually did.

        Keep it to a lookup. Parsing belongs in `contribute`.
        """
        ...

    async def contribute(self, intent: IntentObject, ctx: RunContext) -> KnowledgeContribution:
        """Produce this provider's knowledge, in the shared vocabulary."""
        ...


def precedence_of(name: str) -> int:
    """Rank for `name`. Unknown providers sort after every known one.

    Deliberately not an error: an experimental provider should be able to participate
    without editing the policy list, and it should lose every conflict while it does.
    """
    try:
        return PRECEDENCE.index(name)
    except ValueError:
        return len(PRECEDENCE)


def order_by_precedence(names: tuple[str, ...]) -> tuple[str, ...]:
    """Sort provider names by authority, highest first.

    Used to enforce that a `ResolutionPlan` narrows the default order rather than
    reordering it: the resolver chooses *who participates*, this decides *who wins*.
    """
    return tuple(sorted(names, key=lambda name: (precedence_of(name), name)))
