from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

RunEventType = Literal[
    "stage",  # a named phase started or finished
    "todo_update",  # the agent revised its plan
    "tool_call",  # a tool was invoked
    "tool_result",  # a tool returned
    "input_required",  # the run suspended; the user must answer
    "complete",  # the run finished
    "error",  # the run failed
]

#: Terminal events: nothing follows them on this run.
TERMINAL: frozenset[str] = frozenset({"input_required", "complete", "error"})


class RunEventOut(BaseModel):
    """One event, as the client sees it.

    `seq` is monotonic within a run and is what a reconnecting client resumes from.
    """

    model_config = ConfigDict(frozen=True)

    seq: int
    event_type: RunEventType
    payload: dict[str, object]
    created_at: datetime
