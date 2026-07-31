"""Contract date arithmetic. Deterministic, so the model never computes a term end date.

An LLM asked to add three years to 29 February 2028 will usually be right. "Usually" is not
a property a contract term can have.

Imports neither `agents` nor `openai`.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

__all__ = ["ContractDates", "add_duration", "format_long", "parse_date", "parse_duration"]

_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


class ContractDates(dict[str, str]):
    """A plain str->str mapping, so it drops straight into clause variables."""


def parse_date(value: str) -> date:
    """Accept `YYYY-MM-DD` or `1 August 2026`."""
    text = value.strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass

    parts = text.replace(",", " ").split()
    if len(parts) == 3:
        day, month, year = parts
        for index, name in enumerate(_MONTHS, start=1):
            if name.lower().startswith(month.lower()[:3]):
                try:
                    return date(int(year), index, int(day))
                except ValueError as exc:
                    raise ValueError(f"not a real date: {value!r}") from exc
    raise ValueError(f"unrecognised date {value!r}; use YYYY-MM-DD or '1 August 2026'")


def parse_duration(value: str) -> tuple[int, str]:
    """`'3 years'` -> `(3, 'years')`. Accepts years, months, days, singular or plural."""
    parts = value.strip().lower().split()
    if len(parts) != 2 or not parts[0].isdigit():
        raise ValueError(f"unrecognised duration {value!r}; use e.g. '3 years' or '18 months'")

    amount, unit = int(parts[0]), parts[1].rstrip(".")
    unit = unit if unit.endswith("s") else unit + "s"
    if unit not in {"years", "months", "days"}:
        raise ValueError(f"duration unit must be years, months or days, got {parts[1]!r}")
    if amount < 1:
        raise ValueError("duration must be at least 1")
    return amount, unit


def _add_months(start: date, months: int) -> date:
    total = start.month - 1 + months
    year = start.year + total // 12
    month = total % 12 + 1
    # 31 January + 1 month is 28/29 February, not 3 March.
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def add_duration(start: date, amount: int, unit: str) -> date:
    if unit == "days":
        return start + timedelta(days=amount)
    if unit == "months":
        return _add_months(start, amount)
    # 29 February + 3 years is 28 February, not 1 March.
    return _add_months(start, amount * 12)


def format_long(value: date) -> str:
    return f"{value.day} {_MONTHS[value.month - 1]} {value.year}"


def contract_dates(effective_date: str, duration: str) -> ContractDates:
    start = parse_date(effective_date)
    amount, unit = parse_duration(duration)
    end = add_duration(start, amount, unit)

    result = ContractDates(
        effective_date=format_long(start),
        effective_date_iso=start.isoformat(),
        term_end_date=format_long(end),
        term_end_date_iso=end.isoformat(),
        duration=f"{amount} {unit}",
    )
    if unit == "years":
        result["duration_years"] = str(amount)
    return result
