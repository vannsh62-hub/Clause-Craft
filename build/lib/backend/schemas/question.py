from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

QuestionType = Literal["date", "text", "duration", "money", "enum"]


class Question(BaseModel):
    """One thing the agent needs from the user before it can draft."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="the clause variable this answers, e.g. `effective_date`")
    question: str = Field(description="the question, phrased for a non-lawyer")
    type: QuestionType = Field(description="how the UI should render the input")
