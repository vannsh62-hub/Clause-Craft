"""The event stream.

`GET /runs/{id}/events?seq=N` replays everything after `seq` from Postgres, then tails. That
is the entire reconnection story: a client that drops mid-run reconnects with the last `seq`
it saw and misses nothing, because the agent task wrote every event before telling anyone.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Query
from sse_starlette.sse import EventSourceResponse

from backend.api.deps import get_session_factory
from backend.api.events import stream_run_events
from backend.workspace.models import Run

router = APIRouter(tags=["runs"])


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: uuid.UUID,
    seq: int = Query(0, ge=0, description="last seq the client already has"),
) -> EventSourceResponse:
    factory = get_session_factory()
    async with factory() as session:
        if await session.get(Run, run_id) is None:
            raise HTTPException(status_code=404, detail="no such run")

    async def publish() -> AsyncIterator[dict[str, str]]:
        async for event in stream_run_events(factory, run_id, seq):
            yield {
                "id": str(event.seq),
                "event": event.event_type,
                "data": json.dumps(event.payload, default=str),
            }

    return EventSourceResponse(publish())
