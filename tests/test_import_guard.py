"""The OpenAI Agents SDK's import root is `agents`.

If `backend/` ever lands on `sys.path` — a stray PYTHONPATH, a pytest rootdir, running a
script from inside `backend/` — then `from agents import Agent` would silently import our
own package instead of the SDK. That failure is environment-dependent and miserable to
debug, so we assert against it rather than hope.

This is why `backend/agents/` was renamed to `backend/subagents/`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def test_agents_resolves_to_the_sdk_not_our_package() -> None:
    spec = importlib.util.find_spec("agents")
    assert spec is not None, "openai-agents is not installed"
    assert spec.origin is not None

    origin = Path(spec.origin).resolve()
    repo_root = Path(__file__).resolve().parent.parent

    # A venv may live inside the repo, so "under repo_root" is not the test.
    # The test is: it came from an installed distribution, not from backend/.
    assert "site-packages" in origin.parts, (
        f"`import agents` resolved to {origin}, which is not an installed package. "
        "The SDK is being shadowed by local source."
    )
    assert (repo_root / "backend") not in origin.parents


def test_repo_exposes_no_top_level_agents_package() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    assert not (repo_root / "agents").exists()
    assert not (repo_root / "backend" / "agents").exists(), (
        "backend/agents/ shadows the SDK import root; it must stay named subagents/"
    )


def test_backend_is_not_on_sys_path() -> None:
    backend = (Path(__file__).resolve().parent.parent / "backend").resolve()
    on_path = [p for p in sys.path if p and Path(p).resolve() == backend]
    assert not on_path, f"backend/ is on sys.path via {on_path}; it will shadow `agents`"
