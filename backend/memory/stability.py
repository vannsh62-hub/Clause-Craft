"""What may be remembered, and for how long.

**Allow-list, not deny-list.** A key not in `MEMORABLE` is refused. A deny-list needs to
anticipate every dangerous key; an allow-list needs to anticipate every safe one, and being
wrong about a safe key costs a question rather than a contract.

**Keys are profile facts, not clause variables.** This is the one place the spec needed
correcting. `disclosing_party` is *your* company in one NDA and the counterparty in the next —
so remembering it under that name would write your own company in as the other side. Memory
stores `my_company_name`; the orchestrator decides which slot it fills, and discloses that it
did.

Anything deal-specific — a counterparty, an effective date, a fee — is simply absent from this
table, and the store refuses it. There is no `per_deal` enum value to get wrong.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

__all__ = ["MEMORABLE", "Stability", "is_memorable", "stale_after", "stability_of"]

Stability = Literal["stable", "volatile"]

#: How long a fact of each class is trusted before it must be re-confirmed.
_HALF_LIFE: dict[Stability, timedelta] = {
    # A registered legal name outlives most employees.
    "stable": timedelta(days=730),
    # A signatory leaves; a preferred term changes with the last negotiation.
    "volatile": timedelta(days=180),
}

#: Every key the system may remember. Anything else is deal-specific by definition.
MEMORABLE: dict[str, Stability] = {
    # Who the user is.
    "my_company_name": "stable",
    "my_company_address": "stable",
    # Who signs for them. People leave.
    "my_signatory": "volatile",
    # How they like their contracts. Preferences, not facts about a deal.
    "preferred_governing_law_country": "volatile",
    "preferred_jurisdiction_city": "volatile",
    "preferred_duration_years": "volatile",
    "preferred_payment_days": "volatile",
    "preferred_notice_days": "volatile",
    "preferred_currency": "volatile",
}

#: Named only so the refusal message can be specific. Never stored; not an enum value.
_NEVER_MEMORABLE_EXAMPLES = (
    "effective_date",
    "term_end_date",
    "disclosing_party",
    "receiving_party",
    "fee_amount",
    "liability_cap",
    "services_description",
)


def is_memorable(key: str) -> bool:
    return key in MEMORABLE


def stability_of(key: str) -> Stability:
    return MEMORABLE[key]


def stale_after(key: str) -> timedelta:
    return _HALF_LIFE[MEMORABLE[key]]


def refusal_reason(key: str) -> str:
    """Why a key was refused, phrased for the model that tried to store it."""
    if key in _NEVER_MEMORABLE_EXAMPLES:
        return (
            f"'{key}' is specific to one deal and is never remembered. "
            "Ask the user for it every time."
        )
    return (
        f"'{key}' is not a memorable key. Memory holds who the user is and how they like their "
        f"contracts, never the particulars of a deal. Known keys: {sorted(MEMORABLE)}."
    )
