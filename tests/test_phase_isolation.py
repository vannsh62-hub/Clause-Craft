"""Phase B may not import Phase A, except the shared schemas.

The signature test proves nothing else is *passed* to Phase B. This proves Phase B does not
*reach* for it. A drafting stage that did `from backend.phase_a.gather import ...` to
re-run a provider, or imported the template parser to re-read the uploaded document, would
satisfy the signature test perfectly and still have defeated the boundary — it would just
be fetching the knowledge itself instead of being handed it.

So the import graph is checked, the same way `test_invariants_are_llm_free.py` checks the
invariants layer: by AST, because the failure is quiet and the guarantee is worth asserting
rather than hoping for.

What Phase B *may* import: the schemas both phases share (`backend.schemas.*`), the runtime
port, the invariants (which import no framework and no phase), and the workspace/artifact
plumbing. What it may not: anything under `backend.phase_a` or `backend.knowledge`, which are
where knowledge is *gathered* — Phase B's job is to consume what was already gathered.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PHASE_B = Path(__file__).resolve().parent.parent / "backend" / "phase_b"

#: Packages Phase B must not depend on. `phase_a` gathers and interprets knowledge;
#: `knowledge` is the provider machinery. Phase B receives their distilled result as a CKO
#: and must not go back to the source.
FORBIDDEN_PREFIXES = ("backend.phase_a", "backend.knowledge")

#: The one exception, if it were ever needed: the CKO schema itself. It lives in
#: `backend.schemas`, not `backend.phase_a`, so no exception is actually required — but
#: naming it documents the intent, and catches a future refactor that moves the schema.
ALLOWED_SCHEMA = "backend.schemas.cko"


def _imports(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def _phase_b_files() -> list[Path]:
    return sorted(PHASE_B.glob("*.py"))


@pytest.mark.parametrize("path", _phase_b_files(), ids=lambda p: f"phase_b/{p.name}")
def test_phase_b_does_not_import_phase_a(path: Path) -> None:
    offending = {
        module
        for module in _imports(path.read_text())
        if any(module == p or module.startswith(p + ".") for p in FORBIDDEN_PREFIXES)
        and module != ALLOWED_SCHEMA
    }
    assert not offending, (
        f"phase_b/{path.name} imports {sorted(offending)}. Phase B consumes the CKO; it may "
        "not reach back into the knowledge-gathering layer to re-fetch what Phase A already "
        "distilled. If a fact is missing, it belongs in the CKO."
    )


@pytest.mark.parametrize("path", _phase_b_files(), ids=lambda p: f"phase_b/{p.name}")
def test_phase_b_imports_no_agent_framework(path: Path) -> None:
    """Phase B orchestrates agents through the runtime port, never the SDK directly.

    The same rule the port itself lives by. An `import agents` here would weld a drafting
    stage to one engine, which is the thing the port exists to prevent.
    """
    forbidden = {"agents", "openai", "deepagents", "langchain", "langgraph"}
    offending = {m.split(".")[0] for m in _imports(path.read_text())} & forbidden
    assert not offending, (
        f"phase_b/{path.name} imports {sorted(offending)}. Run agents through "
        "backend.runtime, not the SDK."
    )


def test_the_guard_covers_a_real_module() -> None:
    """Guard against the guard passing because it found nothing to check."""
    files = _phase_b_files()
    assert any(p.name == "engine.py" for p in files), "phase_b/engine.py must exist to be checked"


def test_the_check_would_actually_catch_a_violation() -> None:
    bad = "from backend.phase_a.gather import gather\nimport agents\n"
    modules = _imports(bad)
    assert "backend.phase_a.gather" in modules
    assert "agents" in {m.split(".")[0] for m in modules}

    allowed = "from backend.schemas.cko import ContractKnowledgeObject\n"
    assert _imports(allowed) == {"backend.schemas.cko"}
