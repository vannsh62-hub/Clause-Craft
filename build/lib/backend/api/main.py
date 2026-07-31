from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routers import clauses, contracts, exports, memory, playbook, runs
from backend.api.routers import extract as extract_router
from backend.core.config import settings
from backend.core.logging import configure
from backend.core.sdk import configure_sdk

# NOTE: on Windows, this app must be served through run_server.py (repo root), not
# `uvicorn backend.api.main:app` directly. run_server.py creates the SelectorEventLoop
# that async Psycopg requires; the default ProactorEventLoop cannot run it.

configure(settings.log_level)
configure_sdk()  # tracing off: spans carry contract text and party names

API_PREFIX = "/api/v1"

app = FastAPI(
    title="AI Contract Drafting Platform",
    version="0.1.0",
    description=(
        "A deep agent that drafts contracts from a library of counsel-approved clauses. "
        "It asks rather than guesses, and no document can exist for a draft that failed "
        "the deterministic gates."
    ),
)

# Single-tenant, no auth: a deliberate MVP scope decision. The frontend is served separately.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(contracts.router, prefix=API_PREFIX)
app.include_router(runs.router, prefix=API_PREFIX)
app.include_router(clauses.router, prefix=API_PREFIX)
app.include_router(extract_router.router, prefix=API_PREFIX)
app.include_router(exports.router, prefix=API_PREFIX)
app.include_router(memory.router, prefix=API_PREFIX)
app.include_router(playbook.router, prefix=API_PREFIX)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
