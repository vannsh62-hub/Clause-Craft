"""Launcher for the API server.

Windows defaults asyncio to ProactorEventLoop, which async Psycopg cannot run under.
Setting `asyncio.set_event_loop_policy(...)` before starting uvicorn is the usual fix, but
it turned out not to be reliable here -- depending on the Python version, `asyncio.run()`
(which is what `uvicorn.run()` uses internally) doesn't always go back through the policy
object to build its loop.

So instead of trying to influence *how* the loop gets created, this script creates the
correct loop itself and drives uvicorn's server coroutine on it directly with
`loop.run_until_complete(...)`, skipping `asyncio.run()`/`uvicorn.run()` altogether. That
sidesteps the policy question completely -- no ambiguity about which factory ends up
being used.

Usage (from the project root, with your venv active):
    python run_server.py
"""

from __future__ import annotations

import asyncio
import sys

import uvicorn


async def _serve() -> None:
    config = uvicorn.Config("backend.api.main:app", host="127.0.0.1", port=8000, reload=False, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_serve())
        finally:
            loop.close()
    else:
        asyncio.run(_serve())
