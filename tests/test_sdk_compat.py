"""Canary for the openai / openai-agents pairing.

`openai-agents` declares `openai<3,>=2.36`, but that range is not trustworthy: `openai`
2.45.0 made `InputTokensDetails.cache_write_tokens` a required field, which broke
`agents.usage.Usage()` on `openai-agents` 0.18.0 — a combination the declared constraint
happily permits. The symptom is that *every* agent run fails when constructing a run
context, which is a miserable thing to discover after the first token is spent.

These tests construct the objects the SDK builds on every run, and assert the API surface
this codebase actually depends on. They cost nothing and need no network. When a dependency
bump breaks the pairing, this file goes red first.
"""

from __future__ import annotations

import inspect

from agents import Agent, ModelSettings, Runner, function_tool
from agents.tool_context import ToolContext
from agents.usage import Usage


def test_usage_constructs() -> None:
    """Broken by openai 2.45.0 + openai-agents 0.18.0."""
    assert Usage().total_tokens == 0


def test_tool_context_constructs() -> None:
    ctx = ToolContext(
        context=None, usage=Usage(), tool_name="t", tool_call_id="call_1", tool_arguments="{}"
    )
    assert ctx.tool_call_id == "call_1"


def test_as_tool_exposes_the_parameters_the_orchestrator_needs() -> None:
    """Sub-agents are agents-as-tools, capped, with bounded output extraction."""
    params = inspect.signature(Agent.as_tool).parameters
    for required in ("tool_name", "tool_description", "custom_output_extractor", "max_turns"):
        assert required in params, f"Agent.as_tool lost `{required}`"


def test_runner_run_accepts_context_session_and_max_turns() -> None:
    params = inspect.signature(Runner.run).parameters
    for required in ("context", "session", "max_turns"):
        assert required in params, f"Runner.run lost `{required}`"


def test_model_settings_can_disable_parallel_tool_calls() -> None:
    """Serialises workspace writes and stops the model batching several ask_user calls."""
    assert ModelSettings(parallel_tool_calls=False).parallel_tool_calls is False


def test_function_tool_accepts_a_failure_error_function() -> None:
    params = inspect.signature(function_tool).parameters
    assert "failure_error_function" in params


def test_sqlalchemy_session_is_available_for_postgres() -> None:
    from agents.extensions.memory import SQLAlchemySession

    assert hasattr(SQLAlchemySession, "from_url")


def test_the_exceptions_we_catch_still_exist() -> None:
    from agents.exceptions import AgentsException, MaxTurnsExceeded, ModelBehaviorError

    assert issubclass(MaxTurnsExceeded, AgentsException)
    assert issubclass(ModelBehaviorError, AgentsException)
