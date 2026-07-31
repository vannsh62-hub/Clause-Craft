"""Loop detection for tool calls.

An autonomous agent that wedges will re-issue the same call forever. `max_turns` eventually
stops it, but only after forty turns of paid tokens. This stops it after three.

The fingerprint binds the *declared signature*, not `**kwargs`: the SDK invokes tools with
positional arguments, so a naive `kwargs`-only fingerprint collapses every call of a tool to
one key and a second, legitimately different call trips the detector. Arguments are
canonicalised with sorted keys, or two identical calls would hash differently.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

from agents import RunContextWrapper

from backend.core.run_context import RunContext
from backend.schemas.errors import LoopDetected

__all__ = ["MAX_IDENTICAL_CALLS", "fingerprint", "loop_guard"]

#: The call that trips the guard. Two identical calls are a retry; three is a loop.
MAX_IDENTICAL_CALLS = 3

P = ParamSpec("P")
R = TypeVar("R")


def fingerprint(tool_name: str, arguments: dict[str, Any]) -> str:
    canonical = json.dumps(arguments, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(f"{tool_name}\x00{canonical}".encode()).hexdigest()


def loop_guard(
    fn: Callable[P, Awaitable[R]],
) -> Callable[P, Awaitable[R]]:
    """Refuse the third identical call to `fn` within a run slice.

    Wraps with `functools.wraps`, so `@function_tool` still reads the inner signature and
    docstring when it builds the JSON schema.
    """
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()

        arguments = dict(bound.arguments)
        ctx_name = next(iter(signature.parameters))
        ctx_wrapper = arguments.pop(ctx_name)

        if not isinstance(ctx_wrapper, RunContextWrapper):  # pragma: no cover - misuse
            raise TypeError(f"{fn.__name__} must take RunContextWrapper as its first parameter")

        context: RunContext = ctx_wrapper.context
        key = fingerprint(fn.__name__, arguments)
        seen = context.tool_call_log.get(key, 0) + 1
        context.tool_call_log[key] = seen

        if seen >= MAX_IDENTICAL_CALLS:
            raise LoopDetected(
                f"{fn.__name__} has been called {seen} times with identical arguments. "
                "Repeating it will not change the result — take a different action, "
                "or ask the user."
            )

        return await fn(*args, **kwargs)

    return wrapper
