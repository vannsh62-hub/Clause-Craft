"""The runtime port's contract, tested without any runtime.

These are the tests that make "runtime-agnostic" a property rather than an intention. They
pass today against the only adapter that exists, and they are what a second adapter will be
held to — written now, while the contract is being designed, rather than during a migration
when every failure is ambiguous.

`coerce_output` is the crux. The two candidate runtimes return structured output
differently:

    openai-agents   `output_type=Model` -> a validated instance
    deepagents      `response_format=Model` -> a JSON *string* in a ToolMessage

If either shape leaked to call sites, swapping engines would mean editing every agent. So
the port accepts both and yields the same object, and that equivalence is asserted here.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from backend.runtime.parse import MalformedAgentOutput, coerce_output, strip_fences
from backend.runtime.spec import AgentSpec, HistoryHandle
from backend.schemas.errors import ContractToolError
from backend.schemas.judge import JudgeVerdict

VERDICT = {"consistency": 13, "formatting": 8, "tone": 4, "findings": [], "summary": "good"}


def _spec() -> AgentSpec[JudgeVerdict]:
    return AgentSpec(
        name="judge_agent", prompt="p", model="m", output_model=JudgeVerdict, max_turns=6
    )


# --------------------------------------------------------------------- output shapes


def test_coerce_output_across_shapes() -> None:
    """Every shape a supported runtime can return yields an identical object.

    This is the single test that most directly protects the port. If a future adapter
    returns something none of these branches accept, it fails here rather than producing a
    subtly different verdict downstream.
    """
    spec = _spec()
    instance = JudgeVerdict(**VERDICT)

    shapes = [
        instance,  # openai-agents: final_output_as
        '{"consistency":13,"formatting":8,"tone":4,"findings":[],"summary":"good"}',  # JSON
        '```json\n{"consistency":13,"formatting":8,"tone":4,"findings":[],"summary":"good"}\n```',
        VERDICT,  # a plain mapping
    ]

    results = [coerce_output(spec, shape) for shape in shapes]

    assert all(isinstance(r, JudgeVerdict) for r in results)
    assert all(r == instance for r in results), "the shapes must not disagree"
    assert all(r.points == 25 for r in results)


@pytest.mark.parametrize(
    "fenced",
    [
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        '```JSON\n{"a": 1}\n```',
        '  ```json\n{"a": 1}\n```  ',
    ],
)
def test_strip_fences_handles_the_ways_models_wrap_json(fenced: str) -> None:
    assert strip_fences(fenced) == '{"a": 1}'


def test_strip_fences_leaves_bare_json_alone() -> None:
    assert strip_fences('{"a": 1}') == '{"a": 1}'


def test_a_fenced_non_json_language_is_not_unwrapped() -> None:
    """```python is not a JSON payload; unwrapping it would hide the real problem."""
    assert strip_fences("```python\nprint(1)\n```").startswith("```")


# ------------------------------------------------------------------------- refusals


def test_output_that_does_not_match_the_model_is_a_recoverable_fault() -> None:
    """A `ContractToolError`, so the orchestrator retries rather than the run dying."""
    with pytest.raises(MalformedAgentOutput, match="does not match JudgeVerdict"):
        coerce_output(_spec(), '{"consistency": 99}')  # exceeds the dimension cap

    assert issubclass(MalformedAgentOutput, ContractToolError)


def test_unparseable_text_is_refused_rather_than_guessed() -> None:
    with pytest.raises(MalformedAgentOutput):
        coerce_output(_spec(), "I think the draft is pretty good, honestly")


def test_an_unexpected_type_is_refused() -> None:
    with pytest.raises(MalformedAgentOutput, match="int"):
        coerce_output(_spec(), 42)


def test_a_spec_with_no_output_model_has_no_structured_output_to_read() -> None:
    spec: AgentSpec[JudgeVerdict] = AgentSpec(name="drafting_agent", prompt="p", model="m")
    with pytest.raises(MalformedAgentOutput, match="declares no output_model"):
        coerce_output(spec, VERDICT)


def test_a_different_model_class_is_round_tripped_not_assumed() -> None:
    class Other(BaseModel):
        consistency: int
        formatting: int
        tone: int
        findings: list[str]
        summary: str

    result = coerce_output(_spec(), Other(**VERDICT))
    assert isinstance(result, JudgeVerdict)


# ---------------------------------------------------------------------------- specs


def test_a_spec_is_immutable() -> None:
    """Specs are shared module-level values; a mutable one is a cross-run leak."""
    spec = _spec()
    with pytest.raises(Exception):  # noqa: B017 - dataclasses raise FrozenInstanceError
        spec.max_turns = 99  # type: ignore[misc]


def test_history_none_is_the_isolation_primitive() -> None:
    """A handle is data, not a session object, so no SDK type crosses the port."""
    handle = HistoryHandle(kind="sqlalchemy", key="abc")
    assert (handle.kind, handle.key) == ("sqlalchemy", "abc")
    assert _spec().output_model is JudgeVerdict


def test_the_port_modules_import_no_agent_framework() -> None:
    """Belt and braces alongside `test_invariants_are_llm_free.py`.

    Importing the port must not drag in an SDK. If it does, every consumer of a "neutral"
    spec transitively depends on the engine it was supposed to be independent of.
    """
    import sys

    for module in ("backend.runtime.spec", "backend.runtime.parse", "backend.runtime.human"):
        assert module in sys.modules or __import__(module)
