"""A missing OPENAI_API_KEY must fail at config load, not thirty seconds into a
drafting run at the first model call.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.core.config import Settings


def test_missing_api_key_raises() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, openai_api_key=None)  # type: ignore[arg-type]


@pytest.mark.parametrize("placeholder", ["", "   ", "changeme", "your-key-here"])
def test_placeholder_api_key_raises(placeholder: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, openai_api_key=placeholder)


def test_non_postgres_database_url_raises() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, openai_api_key="sk-x", database_url="mysql://a/b")


def test_hard_stops_have_the_values_the_safety_argument_assumes() -> None:
    s = Settings(_env_file=None, openai_api_key="sk-x")
    assert s.max_draft_attempts == 3
    assert s.max_turns == 40
    assert s.judge_pass_score == 90
