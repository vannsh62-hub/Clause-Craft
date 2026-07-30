from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

TodoStatus = Literal["pending", "in_progress", "done", "cancelled"]


class Todo(BaseModel):
    model_config = ConfigDict(frozen=True)

    task: str
    status: TodoStatus = "pending"
