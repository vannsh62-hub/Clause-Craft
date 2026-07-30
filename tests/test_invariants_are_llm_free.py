"""backend/invariants/ carries the entire safety argument.

It must never import `agents` or `openai`. If the SDK is not in scope, the correctness
layer cannot accidentally acquire an LLM dependency — nobody can slip a model call into
`validate_draft` or `render_clause` without this test going red.

This is enforced by AST inspection rather than by convention, because the failure mode
(a nondeterministic gate in a legal tool) is severe and silent.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

#: Extended beyond `agents`/`openai` for spec 05: the runtime port exists so the engine can
#: be swapped, which is worth nothing if the neutral half quietly imports one. Naming the
#: candidate replacements too means adopting `deepagents` later cannot start by leaking it
#: into the layer that was supposed to be independent of it.
FORBIDDEN = {"agents", "openai", "deepagents", "langchain", "langgraph"}

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
INVARIANTS = _BACKEND / "invariants"

#: The framework-free half of the runtime port. `adapters/` is deliberately excluded —
#: that is the only place an SDK import is allowed to appear.
PORT = [
    _BACKEND / "runtime" / name
    for name in ("spec.py", "parse.py", "human.py", "port.py", "__init__.py")
]


def _imported_roots(source: str) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("path", sorted(INVARIANTS.glob("*.py")), ids=lambda p: p.name)
def test_invariant_module_imports_no_llm_sdk(path: Path) -> None:
    forbidden = _imported_roots(path.read_text()) & FORBIDDEN
    assert not forbidden, (
        f"{path.name} imports {sorted(forbidden)}. The correctness layer must stay "
        "model-free; put the @function_tool adapter in backend/tools/ instead."
    )


@pytest.mark.parametrize("path", PORT, ids=lambda p: f"runtime/{p.name}")
def test_the_runtime_port_imports_no_agent_framework(path: Path) -> None:
    """The port must not know which engine it runs on.

    A port that imports an SDK is not a port, it is a re-export. Every agent definition
    depends on these modules, so one import here welds the whole system to one runtime —
    which is the exact outcome the port was built to avoid.
    """
    forbidden = _imported_roots(path.read_text()) & FORBIDDEN
    assert not forbidden, (
        f"runtime/{path.name} imports {sorted(forbidden)}. Framework code belongs in "
        "backend/runtime/adapters/, which is the only place it is allowed."
    )


def test_the_adapter_is_where_the_framework_lives() -> None:
    """The complement: the exclusion above is real, not vacuous.

    If the adapter stopped importing `agents`, these guards would still pass while proving
    nothing — the port would look clean because no runtime existed at all.
    """
    adapter = _BACKEND / "runtime" / "adapters" / "openai_agents" / "runner.py"
    assert "agents" in _imported_roots(adapter.read_text())


def test_the_check_would_actually_catch_a_violation() -> None:
    assert _imported_roots("import agents") & FORBIDDEN == {"agents"}
    assert _imported_roots("from openai import OpenAI") & FORBIDDEN == {"openai"}
    assert _imported_roots("from agents.run import Runner") & FORBIDDEN == {"agents"}
    assert not _imported_roots("from backend.schemas.clause import Clause") & FORBIDDEN
