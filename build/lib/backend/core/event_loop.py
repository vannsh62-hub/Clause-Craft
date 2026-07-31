"""Event-loop factories used by the API server."""

from __future__ import annotations

import asyncio


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    """Return the event loop required by async Psycopg on Windows."""
    return asyncio.SelectorEventLoop()
