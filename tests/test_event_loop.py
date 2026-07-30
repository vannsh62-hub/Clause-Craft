from __future__ import annotations

import asyncio

from backend.core.event_loop import selector_loop_factory


def test_selector_loop_factory() -> None:
    loop = selector_loop_factory()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()
