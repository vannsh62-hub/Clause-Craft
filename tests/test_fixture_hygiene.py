"""Fixtures that clean up must do it in `try/finally`.

This suite shares one real Postgres. Isolation comes from fixtures deleting what they
created — so a fixture that fails to clean up does not merely leak a row, it changes the
behaviour of every test that runs afterwards.

The trap is specific and easy to walk into. Written the obvious way:

    yield store
    await session.execute(delete(...))      # never runs when the test fails
    await session.commit()

pytest throws a failing test's exception *into* the generator at the `yield`, so anything
after it is skipped — precisely when a test has failed, which is exactly when it is most
likely to have left rows behind. One failure then leaks state into unrelated files, where
it surfaces as a second, misleading failure somewhere else entirely.

That is not hypothetical here. It caused two failures during this rebuild: a leaked
`memory_facts` row made an unscoped `scalar_one()` in a *different* test file raise
`MultipleResultsFound`, and the resulting report pointed at the innocent test. Both halves
are now fixed, and this test stops the fixture half coming back.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent

#: Calls that mean a fixture is undoing something. A fixture doing any of these after its
#: `yield` has cleanup worth protecting.
_CLEANUP = ("delete(", "commit", "dispose", "clear_session", "unregister", "drop")


def _fixtures_with_unprotected_cleanup(source: str) -> list[str]:
    offenders: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        if not any("fixture" in ast.unparse(d) for d in node.decorator_list):
            continue

        rendered = ast.unparse(node)
        if "yield" not in rendered:
            continue

        after_yield = rendered.split("yield", 1)[1]
        if not any(marker in after_yield for marker in _CLEANUP):
            continue

        protected = any(
            isinstance(inner, ast.Try)
            and inner.finalbody
            and any(isinstance(y, ast.Yield | ast.YieldFrom) for y in ast.walk(inner))
            for inner in ast.walk(node)
        )
        if not protected:
            offenders.append(f"{node.name} (line {node.lineno})")
    return offenders


@pytest.mark.parametrize("path", sorted(TESTS.glob("*.py")), ids=lambda p: p.name)
def test_fixture_cleanup_is_protected(path: Path) -> None:
    offenders = _fixtures_with_unprotected_cleanup(path.read_text())
    assert not offenders, (
        f"{path.name}: {', '.join(offenders)} clean up after `yield` without `try/finally`. "
        "A failing test skips that cleanup and leaks state into later tests, which then "
        "fail for reasons that have nothing to do with them. Wrap the yield:\n"
        "    try:\n        yield thing\n    finally:\n        ...cleanup..."
    )


def test_the_check_would_actually_catch_a_violation() -> None:
    """Guard against the guard silently matching nothing."""
    bad = (
        "import pytest_asyncio\n"
        "@pytest_asyncio.fixture\n"
        "async def thing():\n"
        "    yield 1\n"
        "    await session.commit()\n"
    )
    good = (
        "import pytest_asyncio\n"
        "@pytest_asyncio.fixture\n"
        "async def thing():\n"
        "    try:\n"
        "        yield 1\n"
        "    finally:\n"
        "        await session.commit()\n"
    )
    no_cleanup = "import pytest\n@pytest.fixture\ndef thing():\n    yield 1\n"

    # `node.lineno` is the `def`, not the decorator above it.
    assert _fixtures_with_unprotected_cleanup(bad) == ["thing (line 3)"]
    assert _fixtures_with_unprotected_cleanup(good) == []
    assert _fixtures_with_unprotected_cleanup(no_cleanup) == []
