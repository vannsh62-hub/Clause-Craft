"""Evaluating playbook rules.

Rules are **data**, evaluated by **code**. No model is involved, and that is the point:
"software contracts get 45-day payment terms" is a policy with a right answer, and asking a
language model to apply it introduces a failure mode where there was none.

A rule may require a section, forbid one, set a value, or raise a flag. A rule may **not**
emit contract text. Clause language belongs in the clause library where it is versioned,
approved, and covered by the fidelity gate; a playbook that emits prose is a way to get
unreviewed language into a contract, so `load_rules` refuses it outright.

The condition language is deliberately tiny — field, operator, value — and there is no
`eval`. A playbook is authored by legal ops, stored per tenant, and evaluated server-side;
an expression language would be an injection surface reachable by anyone who can edit a
playbook.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from backend.schemas.errors import ContractToolError
from backend.schemas.playbook import PlaybookRequirement, RequirementKind

__all__ = [
    "PlaybookError",
    "Rule",
    "RuleCondition",
    "evaluate",
    "load_rules",
    "unmet_requirements",
]

Operator = Literal["eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte", "exists"]


class PlaybookError(ContractToolError):
    """A playbook that cannot be loaded or is not a playbook at all."""


class RuleCondition(BaseModel):
    """One condition. `field` is looked up in a flat mapping of facts."""

    model_config = ConfigDict(frozen=True)

    field: str
    op: Operator
    value: Any = None


class Rule(BaseModel):
    """A condition set and what it requires.

    All conditions must hold — `and`, with no `or`. A rule needing disjunction is two
    rules, which reads better in a review and produces a `rule_id` that names the actual
    reason.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    when: tuple[RuleCondition, ...] = ()
    kind: RequirementKind
    target: str
    value: str | None = None
    reason: str = ""
    blocking: bool = True


def _matches(condition: RuleCondition, facts: Mapping[str, Any]) -> bool:
    present = condition.field in facts and facts[condition.field] is not None
    if condition.op == "exists":
        return present is bool(condition.value if condition.value is not None else True)
    if not present:
        # An absent fact satisfies nothing. A rule that fired on missing data would apply
        # GDPR to a contract whose jurisdiction was simply never determined.
        return False

    actual = facts[condition.field]
    expected = condition.value

    if condition.op == "eq":
        return bool(_comparable(actual) == _comparable(expected))
    if condition.op == "ne":
        return bool(_comparable(actual) != _comparable(expected))
    if condition.op == "in":
        return _comparable(actual) in {_comparable(v) for v in expected or ()}
    if condition.op == "not_in":
        return _comparable(actual) not in {_comparable(v) for v in expected or ()}

    try:
        left, right = float(actual), float(expected)
    except (TypeError, ValueError):
        # A numeric comparison against something non-numeric is an authoring error, not a
        # match. Refusing loudly beats silently treating it as false.
        raise PlaybookError(
            f"rule condition on {condition.field!r} compares {actual!r} numerically, "
            "but it is not a number."
        ) from None

    if condition.op == "gt":
        return left > right
    if condition.op == "gte":
        return left >= right
    if condition.op == "lt":
        return left < right
    return left <= right


def _comparable(value: Any) -> Any:
    """Case-insensitive for strings so `"Software"` matches `"software"`."""
    return value.casefold() if isinstance(value, str) else value


def load_rules(raw: Sequence[Mapping[str, Any]]) -> tuple[Rule, ...]:
    """Parse and validate rule data.

    Refuses any rule carrying prose. The heuristic is length: a `value` long enough to be a
    sentence is contract language, and contract language in a playbook has escaped every
    control the clause library provides.
    """
    rules: list[Rule] = []
    for entry in raw:
        try:
            rule = Rule.model_validate(entry)
        except ValidationError as exc:
            raise PlaybookError(f"invalid playbook rule: {entry.get('id', entry)!r}") from exc
        if rule.value is not None and len(rule.value.split()) > 12:
            raise PlaybookError(
                f"rule {rule.id!r} carries clause text. Playbooks hold conditions and "
                "requirements; the language belongs in the clause library."
            )
        rules.append(rule)
    return tuple(rules)


def evaluate(rules: Sequence[Rule], facts: Mapping[str, Any]) -> tuple[PlaybookRequirement, ...]:
    """Return the requirements whose conditions hold.

    Order follows the rule list so the output is stable across runs — a requirement set
    that reshuffles produces a diff on every run and trains reviewers to ignore it.
    """
    return tuple(
        PlaybookRequirement(
            rule_id=rule.id,
            kind=rule.kind,
            target=rule.target,
            value=rule.value,
            reason=rule.reason,
            blocking=rule.blocking,
        )
        for rule in rules
        if all(_matches(condition, facts) for condition in rule.when)
    )


def unmet_requirements(
    draft: str,
    requirements: Iterable[PlaybookRequirement],
) -> tuple[PlaybookRequirement, ...]:
    """Which `require_section` requirements the draft does not satisfy.

    A `require_section` is met when a section for its target appears in the draft — checked
    by looking for the target, and a spaced form of it, in the normalised text. Deliberately
    forgiving on presence: the cost of a false "missing" is a spurious block a human clears,
    while the cost of a false "present" is a contract that ships without a clause the
    playbook required. So the check errs toward flagging.

    Only `require_section` is evaluated here. `set_value` shapes drafting rather than gating
    it; `forbid_section` and `flag` are handled elsewhere. A non-blocking requirement is
    never returned — it was advisory by construction.
    """
    haystack = " ".join(draft.split()).casefold()
    unmet: list[PlaybookRequirement] = []
    for requirement in requirements:
        if requirement.kind != "require_section" or not requirement.blocking:
            continue
        target = requirement.target.casefold()
        spaced = target.replace("_", " ")
        if target not in haystack and spaced not in haystack:
            unmet.append(requirement)
    return tuple(unmet)
