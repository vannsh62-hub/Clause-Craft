"""The return-vs-raise policy, exercised against the real SDK tool machinery.

No model and no network: `FunctionTool.on_invoke_tool` is called directly, which is the
exact code path the SDK takes when the model calls a tool. That is what makes these tests
worth having — they pin the SDK's actual behaviour, not our belief about it.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest
from agents import FunctionTool, function_tool
from agents.tool_context import ToolContext
from agents.usage import Usage

from backend.schemas.draft import Finding
from backend.schemas.errors import (
    MAX_TOOL_ERROR_CHARS,
    AttemptsExhausted,
    Blocked,
    ClauseError,
    ContractToolError,
    ControlSignal,
    CostCeilingExceeded,
    LoopDetected,
    SuspendRun,
    WorkspaceError,
    format_tool_error,
)


def _ctx() -> ToolContext[None]:
    return ToolContext(
        context=None, usage=Usage(), tool_name="t", tool_call_id="call_abc", tool_arguments="{}"
    )


async def _invoke(tool: FunctionTool, **kwargs: object) -> object:
    return await tool.on_invoke_tool(_ctx(), json.dumps(kwargs))


# ------------------------------------------------------------------ the formatter itself


def test_our_own_error_messages_are_echoed_to_the_model() -> None:
    msg = format_tool_error(_ctx(), WorkspaceError("clauses/x.md is read-only"))

    assert "WorkspaceError" in msg
    assert "read-only" in msg


def test_a_foreign_exception_is_never_echoed() -> None:
    """`str(exc)` on a third-party exception leaks. A SQLAlchemy error embeds the statement
    and its bound parameters — the contract text. A connection error embeds the DSN."""
    leaky = RuntimeError(
        "(psycopg.OperationalError) connection to "
        "postgresql://contract:hunter2@db:5432/prod failed; "
        "statement: INSERT INTO contract_versions (markdown) VALUES "
        "('CONFIDENTIAL: ABC Pvt Ltd shall indemnify...')"
    )
    msg = format_tool_error(_ctx(), leaky)

    assert "hunter2" not in msg
    assert "CONFIDENTIAL" not in msg
    assert "INSERT INTO" not in msg
    assert "postgresql://" not in msg
    assert "RuntimeError" in msg  # the class name is safe and useful


def test_error_messages_are_capped_and_single_line() -> None:
    msg = format_tool_error(_ctx(), WorkspaceError("x\n" * 500))

    assert len(msg) <= MAX_TOOL_ERROR_CHARS
    assert "\n" not in msg


def test_a_control_signal_never_becomes_a_model_facing_string() -> None:
    """Belt and braces: these tools register `failure_error_function=None`, so the formatter
    should never see one. If it does, re-raise rather than tell the model to try again."""
    with pytest.raises(SuspendRun):
        format_tool_error(_ctx(), SuspendRun("call_1", [{"name": "effective_date"}]))


# -------------------------------------------- faults: raised, formatted, model can retry


@function_tool(failure_error_function=format_tool_error)
def _tool_that_faults(path: str) -> str:
    raise WorkspaceError(f"{path} is read-only; write to draft_v1.md instead")


async def test_a_fault_reaches_the_model_as_a_hint_not_an_exception() -> None:
    result = await _invoke(_tool_that_faults, path="clauses/x.md")

    assert isinstance(result, str)
    assert "read-only" in result
    assert "draft_v1.md" in result  # the hint survives to the model


@function_tool(failure_error_function=format_tool_error)
def _tool_that_leaks() -> str:
    raise RuntimeError("password=hunter2")


async def test_a_leaky_fault_is_scrubbed_before_the_model_sees_it() -> None:
    result = await _invoke(_tool_that_leaks)

    assert isinstance(result, str)
    assert "hunter2" not in result


# ------------------------------------- control signals: raised THROUGH the tool boundary


@function_tool(failure_error_function=None)
def _tool_that_suspends() -> str:
    raise SuspendRun("call_xyz", [{"name": "effective_date", "question": "When?"}])


async def test_failure_error_function_none_re_raises_past_the_tool_boundary() -> None:
    """The single most load-bearing SDK behaviour in this codebase.

    M7's suspend/resume depends on `SuspendRun` escaping `Runner.run` into our run loop
    rather than being formatted into a message telling the model to try again.
    """
    with pytest.raises(SuspendRun) as exc:
        await _invoke(_tool_that_suspends)

    assert exc.value.call_id == "call_xyz"
    assert exc.value.questions[0]["name"] == "effective_date"


@function_tool(failure_error_function=None)
def _tool_over_budget() -> str:
    raise CostCeilingExceeded(spent_cents=142.0, ceiling_cents=100.0)


async def test_cost_ceiling_escapes_to_our_code() -> None:
    with pytest.raises(CostCeilingExceeded) as exc:
        await _invoke(_tool_over_budget)

    assert exc.value.spent_cents == 142.0


async def test_a_fault_tool_does_NOT_re_raise() -> None:
    """The contrast that gives the previous two tests meaning."""
    result = await _invoke(_tool_that_faults, path="clauses/x.md")
    assert isinstance(result, str), "a ContractToolError must be handled, not propagated"


# ------------------------------------------------- returned outcomes: never raised


@function_tool
def _tool_that_blocks() -> Blocked:
    return Blocked(
        kind="validation",
        hint="Insert the approved text of nda.duration.",
        findings=(
            Finding(
                dimension="completeness",
                severity="blocker",
                message="Required clause 'nda.duration' is absent",
                fix_hint="Insert it in library order.",
                clause_id="nda.duration",
            ),
        ),
    )


async def test_a_blocked_outcome_is_returned_with_its_findings_intact() -> None:
    """Raising this would flatten structured findings into an error string the model must
    re-parse. The agent is supposed to read them and fix the draft."""
    result = await _invoke(_tool_that_blocks)

    payload = result if isinstance(result, Blocked) else Blocked.model_validate_json(str(result))
    assert payload.status == "blocked"
    assert payload.kind == "validation"
    assert payload.findings[0].clause_id == "nda.duration"


def test_blocked_is_not_an_exception() -> None:
    assert not issubclass(Blocked, Exception)


# ------------------------------------------------------------------------- hierarchy


@pytest.mark.parametrize("exc", [WorkspaceError, ClauseError, AttemptsExhausted, LoopDetected])
def test_faults_share_a_base_the_formatter_can_recognise(exc: type[Exception]) -> None:
    assert issubclass(exc, ContractToolError)
    assert not issubclass(exc, ControlSignal)


@pytest.mark.parametrize("exc", [SuspendRun, CostCeilingExceeded])
def test_control_signals_are_not_faults(exc: type[Exception]) -> None:
    assert issubclass(exc, ControlSignal)
    assert not issubclass(exc, ContractToolError)


def test_control_signals_derive_from_exception_not_baseexception() -> None:
    """The SDK's tool wrapper catches `Exception` and only then consults
    `failure_error_function`. A `BaseException` would escape by an untested path."""
    assert issubclass(ControlSignal, Exception)


def test_errors_module_imports_the_sdk_only_under_type_checking() -> None:
    """`backend/invariants` raises these exceptions, and it must not pull in `agents`.

    Every `from agents import ...` in errors.py must be indented — i.e. nested inside the
    `if TYPE_CHECKING:` block — never at module level.
    """
    tree = ast.parse(pathlib.Path("backend/schemas/errors.py").read_text())

    agents_imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.split(".")[0] == "agents"
    ]
    assert agents_imports, "expected the TYPE_CHECKING import; did the module change?"
    for node in agents_imports:
        assert node.col_offset > 0, "`agents` is imported at runtime, not under TYPE_CHECKING"
