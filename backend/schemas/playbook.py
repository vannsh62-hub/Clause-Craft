"""Playbook rules and extracted business rules.

A playbook holds **conditions and requirements, never legal text**. `industry == software
⇒ payment_terms_days = 45` is a rule; the paragraph that expresses 45-day payment is a
clause and belongs in the clause library. A rule that emits clause text is a bug, because
it puts contract language somewhere with no version, no approval, and no fidelity gate.

Requirements flow into both the Transformation Plan and Legal Validation, so an unmet one
blocks finalization rather than merely being noted.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

__all__ = ["BusinessRule", "PlaybookRequirement", "RequirementKind"]

#: What a rule demands. `flag` raises something for a human (legal approval, unusual
#: value); it does not block on its own.
RequirementKind = Literal["require_section", "forbid_section", "set_value", "flag"]


class PlaybookRequirement(BaseModel):
    """One requirement produced by evaluating the playbook against the intent.

    `rule_id` points back at the YAML that produced it. Without it, a blocked
    finalization tells a user *what* failed but not *which policy* said so, which is the
    first question their counsel will ask.
    """

    model_config = ConfigDict(frozen=True)

    rule_id: str
    kind: RequirementKind
    target: str
    value: str | None = None
    reason: str = ""
    blocking: bool = True


class BusinessRule(BaseModel):
    """A rule *observed* in a reference document, as opposed to one imposed by policy.

    Advisory by default: something a previous contract did is evidence, not authority.
    Promoting it to a requirement is a human decision.
    """

    model_config = ConfigDict(frozen=True)

    statement: str
    source: str
    confidence: float = 0.5
