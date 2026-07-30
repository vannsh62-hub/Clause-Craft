"""What the user asked for, and which knowledge sources will answer it.

These are the first two artifacts of Phase A. Both are produced before anything is read
from a document or a clause library, and everything downstream reads them rather than
re-deriving the same judgements.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DealTerm",
    "DraftingMode",
    "IntentObject",
    "Party",
    "ResolutionPlan",
]

#: Which knowledge configuration a run uses. Inferred by the Intent Agent, never selected
#: in the UI — the user says what they want, not how the system should get it.
DraftingMode = Literal["ai_drafting", "template", "library_playbook"]


class Party(BaseModel):
    """A named party to the contract.

    `role` is free text because the vocabulary is contract-type specific — "Disclosing
    Party", "Service Provider", "Employer" — and constraining it would mean maintaining a
    list that is wrong for the next contract type.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    role: str
    jurisdiction: str | None = None


class DealTerm(BaseModel):
    """One operative value the user actually stated — "uptime" = "99.9% per calendar month".

    Without this the drafter never sees the numbers. `IntentObject`'s other fields describe
    the *shape* of the deal (type, parties, law); the terms are its substance, and a drafting
    agent that is forbidden to invent them and is not given them has only bad options: a
    fabricated figure, a placeholder the document gate rejects, or a vague cross-reference
    that quietly drops what the user asked for.

    Free-text `name` and `value` on purpose. The vocabulary is contract-type specific — an
    SLA has uptime and service credits, a lease has rent and a break clause — and a fixed
    schema would be wrong for the next contract type. Verbatim from the request; this is a
    record of what the user said, not an interpretation of it.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    value: str


class IntentObject(BaseModel):
    """The understood request.

    Four fields exist to stop the system guessing, which is the worst thing it can do:

    - `confidence` — below the configured threshold, the agent must ask rather than infer.
    - `needs_clarification` — *specific* questions. "Needs more detail" is not actionable
      and cannot be put to a user.
    - `mode` — decided here, once, so no later stage asks "was a template uploaded?".
    - `primary_source_hint` — a suggestion the Resolver may override. It is a hint and not
      a decision because intent is judged before any source has been inspected.
    """

    model_config = ConfigDict(frozen=True)

    contract_type: str
    parties: tuple[Party, ...] = ()
    #: Operative values stated in the request, carried through the CKO to the drafter.
    deal_terms: tuple[DealTerm, ...] = ()
    country: str | None = None
    jurisdiction: str | None = None
    governing_law: str | None = None
    industry: str | None = None
    language: str = "en"
    purpose: str = ""
    mode: DraftingMode = "ai_drafting"
    primary_source_hint: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    needs_clarification: tuple[str, ...] = ()

    @property
    def must_ask(self) -> bool:
        """True when this intent is not safe to act on."""
        return bool(self.needs_clarification)


class ResolutionPlan(BaseModel):
    """Which knowledge providers participate, in precedence order.

    Answered exactly once, here, so that no downstream stage branches on which sources
    happen to exist. `providers` may **narrow** the default precedence but may never
    reorder it: precedence is a policy decision (a playbook outranks a template, always),
    not a per-run judgement. A resolver that could reorder it could quietly demote the
    playbook, which is how a compliance rule silently stops applying.

    `backend/phase_a/aggregator.py` enforces the ordering rather than trusting it.
    """

    model_config = ConfigDict(frozen=True)

    providers: tuple[str, ...]
    rationale: str = ""
