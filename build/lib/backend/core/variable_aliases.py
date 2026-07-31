"""Shared mapping from deal-term / party names to clause-template variable names.

Clause templates use a fixed vocabulary (`service_provider`, `duration_years`,
`effective_date`, ...). What Phase A actually captures is free text — a deal term named
"Duration" or a party with role "Service Provider" — so without this mapping those values
are always one slugify() away from matching, and clause autofill starts every contract at
0% resolved.

Centralized here so the pipeline (which writes `Contract.variables` once, right after
drafting, in `_record_contract_variables`) and the `/render` endpoint (which self-heals it
on every call) read from the same table instead of drifting apart — that drift is exactly
what silently deleted this logic once already, so don't duplicate the alias table at either
call site.
"""

from __future__ import annotations

import re
from datetime import date, datetime

__all__ = ["resolve_variables"]

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    return _SLUG_RE.sub("_", text.strip().lower()).strip("_")


#: slugified deal-term name or party role -> the clause variable it should feed.
#: Several slugs can point at the same variable (a deal term the user calls "Duration" and
#: one they call "Term" both mean `duration_years` to a clause template). A slug that isn't
#: listed here is kept as-is — this is a set of *known* near-misses, not an allowlist.
_ALIASES: dict[str, str] = {
    "duration": "duration_years",
    "duration_years": "duration_years",
    "term": "duration_years",
    "term_years": "duration_years",
    "contract_duration": "duration_years",
    "notice_period": "notice_days",
    "notice_period_days": "notice_days",
    "notice": "notice_days",
    "termination_notice": "notice_days",
    "effective_date": "effective_date",
    "start_date": "effective_date",
    "commencement_date": "effective_date",
    "agreement_date": "effective_date",
    "service_provider": "service_provider",
    "provider": "service_provider",
    "vendor": "service_provider",
    "supplier": "service_provider",
    "client": "client",
    "customer": "client",
    "disclosing_party": "disclosing_party",
    "discloser": "disclosing_party",
    "receiving_party": "receiving_party",
    "recipient": "receiving_party",
}

#: Formats deal terms and party values are seen in. Tried in order; the first match wins.
_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%B %d, %Y", "%d %B %Y", "%b %d, %Y")


def _parse_date(value: str) -> date | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_years(value: str) -> float | None:
    match = re.search(r"[\d.]+", value)
    return float(match.group()) if match else None


def _add_years(start: date, years: int) -> date:
    try:
        return start.replace(year=start.year + years)
    except ValueError:
        # Feb 29 landing on a non-leap end year — nearest valid day.
        return start.replace(year=start.year + years, day=28)


def resolve_variables(*sources: dict[str, str] | None) -> dict[str, str]:
    """Turn free-text deal terms / party names into clause-template variable names.

    Each positional argument is a name -> value mapping (a deal-term map, a party
    role -> name map, an already-partially-resolved `Contract.variables` dict — any of
    these). Earlier sources win on conflicts. Every key is slugified and passed through
    `_ALIASES` before being kept, so this is safe to call on raw Phase A output, on
    previously-stored `Contract.variables`, or on both together for self-healing.

    `term_end_date` is never stated directly by anyone — it's `effective_date` +
    `duration_years` — so it's derived here whenever both inputs are already known,
    instead of leaving a clause that asks for it permanently blank.
    """
    resolved: dict[str, str] = {}

    for source in sources:
        for name, value in (source or {}).items():
            if not value:
                continue
            slug = _slugify(name)
            variable = _ALIASES.get(slug, slug)
            resolved.setdefault(variable, value)

    if "term_end_date" not in resolved:
        effective = resolved.get("effective_date")
        duration = resolved.get("duration_years")
        if effective and duration:
            start = _parse_date(effective)
            years = _parse_years(duration)
            if start is not None and years is not None:
                resolved["term_end_date"] = _add_years(start, int(years)).isoformat()

    return resolved