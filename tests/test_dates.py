from __future__ import annotations

from datetime import date

import pytest

from backend.invariants.dates import (
    add_duration,
    contract_dates,
    format_long,
    parse_date,
    parse_duration,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-08-01", date(2026, 8, 1)),
        ("1 August 2026", date(2026, 8, 1)),
        ("1 Aug 2026", date(2026, 8, 1)),
        ("29 February 2028", date(2028, 2, 29)),
    ],
)
def test_parse_date(text: str, expected: date) -> None:
    assert parse_date(text) == expected


@pytest.mark.parametrize("bad", ["", "tomorrow", "31 February 2026", "2026-13-01", "1 Foo 2026"])
def test_parse_date_rejects_nonsense(bad: str) -> None:
    with pytest.raises(ValueError, match="date"):
        parse_date(bad)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("3 years", (3, "years")), ("1 year", (1, "years")), ("18 months", (18, "months"))],
)
def test_parse_duration(text: str, expected: tuple[int, str]) -> None:
    assert parse_duration(text) == expected


@pytest.mark.parametrize("bad", ["", "3", "many years", "0 years", "3 fortnights", "-1 years"])
def test_parse_duration_rejects_nonsense(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_duration(bad)


def test_leap_day_plus_three_years_is_28_february() -> None:
    """The reason this is a tool and not a model call."""
    assert add_duration(date(2028, 2, 29), 3, "years") == date(2031, 2, 28)


def test_end_of_month_plus_one_month_does_not_overflow() -> None:
    assert add_duration(date(2026, 1, 31), 1, "months") == date(2026, 2, 28)
    assert add_duration(date(2028, 1, 31), 1, "months") == date(2028, 2, 29)


def test_month_arithmetic_crosses_the_year() -> None:
    assert add_duration(date(2026, 11, 15), 3, "months") == date(2027, 2, 15)


def test_days_are_exact() -> None:
    assert add_duration(date(2026, 8, 1), 90, "days") == date(2026, 10, 30)


def test_format_long() -> None:
    assert format_long(date(2026, 8, 1)) == "1 August 2026"


def test_contract_dates_supplies_the_clause_variables() -> None:
    result = contract_dates("2026-08-01", "3 years")

    assert result["effective_date"] == "1 August 2026"
    assert result["term_end_date"] == "1 August 2029"
    assert result["term_end_date_iso"] == "2029-08-01"
    assert result["duration_years"] == "3"


def test_contract_dates_omits_duration_years_when_the_unit_is_not_years() -> None:
    """`nda.duration` declares `duration_years`. Offering it for an 18-month term would be
    a quietly wrong contract."""
    assert "duration_years" not in contract_dates("2026-08-01", "18 months")


def test_contract_dates_is_deterministic() -> None:
    first = contract_dates("2026-08-01", "3 years")
    for _ in range(10):
        assert contract_dates("2026-08-01", "3 years") == first
