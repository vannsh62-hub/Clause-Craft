"""Playbook evaluation: data in, requirements out, no model anywhere.

"Software contracts get 45-day payment terms" has a right answer. Asking a language model
to apply it would introduce a failure mode where none existed.
"""

from __future__ import annotations

import pytest

from backend.invariants.playbook_rules import PlaybookError, evaluate, load_rules

RULES = load_rules(
    [
        {
            "id": "pay-software-45",
            "when": [{"field": "industry", "op": "eq", "value": "software"}],
            "kind": "set_value",
            "target": "payment_terms_days",
            "value": "45",
            "reason": "standard commercial terms for software",
        },
        {
            "id": "gdpr-eu",
            "when": [{"field": "jurisdiction", "op": "in", "value": ["DE", "FR", "IE"]}],
            "kind": "require_section",
            "target": "GDPR",
            "reason": "EU personal data",
        },
        {
            "id": "dpdp-in",
            "when": [{"field": "jurisdiction", "op": "eq", "value": "IN"}],
            "kind": "require_section",
            "target": "DPDP",
        },
        {
            "id": "approval-high-value",
            "when": [{"field": "contract_value", "op": "gt", "value": 10_000_000}],
            "kind": "flag",
            "target": "legal_approval_required",
            "blocking": False,
        },
    ]
)


def _ids(facts: dict[str, object]) -> set[str]:
    return {r.rule_id for r in evaluate(RULES, facts)}


def test_a_matching_rule_fires() -> None:
    assert "pay-software-45" in _ids({"industry": "software"})


def test_a_non_matching_rule_does_not() -> None:
    assert "pay-software-45" not in _ids({"industry": "healthcare"})


def test_string_matching_ignores_case() -> None:
    assert "pay-software-45" in _ids({"industry": "Software"})


def test_membership_and_numeric_operators() -> None:
    assert "gdpr-eu" in _ids({"jurisdiction": "DE"})
    assert "gdpr-eu" not in _ids({"jurisdiction": "IN"})
    assert "dpdp-in" in _ids({"jurisdiction": "IN"})
    assert "approval-high-value" in _ids({"contract_value": 25_000_000})
    assert "approval-high-value" not in _ids({"contract_value": 5_000})


def test_an_absent_fact_matches_nothing() -> None:
    """A rule that fired on missing data would apply GDPR to a contract whose jurisdiction
    was simply never determined — imposing an obligation from an absence of evidence."""
    assert _ids({}) == set()
    assert _ids({"jurisdiction": None}) == set()


def test_all_conditions_must_hold() -> None:
    rules = load_rules(
        [
            {
                "id": "both",
                "when": [
                    {"field": "industry", "op": "eq", "value": "software"},
                    {"field": "jurisdiction", "op": "eq", "value": "IN"},
                ],
                "kind": "require_section",
                "target": "DPDP",
            }
        ]
    )
    assert evaluate(rules, {"industry": "software"}) == ()
    assert evaluate(rules, {"industry": "software", "jurisdiction": "IN"})


def test_requirements_carry_the_rule_that_produced_them() -> None:
    """A blocked finalization must say *which policy* said so. It is the first question
    the user's counsel will ask."""
    requirement = evaluate(RULES, {"jurisdiction": "IN"})[0]
    assert requirement.rule_id == "dpdp-in"
    assert requirement.target == "DPDP"
    assert requirement.blocking is True


def test_non_blocking_requirements_are_distinguishable() -> None:
    flag = evaluate(RULES, {"contract_value": 25_000_000})[0]
    assert flag.blocking is False


def test_output_order_is_stable() -> None:
    """A requirement set that reshuffles produces a diff on every run, which trains
    reviewers to ignore it."""
    facts = {"industry": "software", "jurisdiction": "IN"}
    assert [r.rule_id for r in evaluate(RULES, facts)] == [
        r.rule_id for r in evaluate(RULES, facts)
    ]


# --------------------------------------------------------------------------- refusals


def test_a_rule_carrying_clause_text_is_refused() -> None:
    """The playbook holds conditions, not language.

    Contract text in a playbook has escaped versioning, approval, and the fidelity gate —
    every control the clause library provides.
    """
    with pytest.raises(PlaybookError, match="carries clause text"):
        load_rules(
            [
                {
                    "id": "bad",
                    "kind": "set_value",
                    "target": "confidentiality",
                    "value": (
                        "The Receiving Party shall hold all Confidential Information in "
                        "strict confidence and shall not disclose it to any third party."
                    ),
                }
            ]
        )


def test_a_malformed_rule_is_refused() -> None:
    with pytest.raises(PlaybookError, match="invalid playbook rule"):
        load_rules([{"id": "no-kind", "target": "x"}])


def test_comparing_a_non_number_numerically_is_an_authoring_error() -> None:
    """Silently treating it as false would make the rule quietly never fire."""
    rules = load_rules(
        [
            {
                "id": "numeric",
                "when": [{"field": "contract_value", "op": "gt", "value": 100}],
                "kind": "flag",
                "target": "x",
            }
        ]
    )
    with pytest.raises(PlaybookError, match="not a number"):
        evaluate(rules, {"contract_value": "quite a lot"})
