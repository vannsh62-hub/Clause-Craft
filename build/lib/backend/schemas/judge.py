"""The LLM judge's verdict.

The judge scores only what code cannot. Completeness, placeholders and fidelity are decided
by `validate_draft` before a token is spent, and the judge **never overrides a deterministic
blocker** — it cannot even see the draft's blocker status.

That leaves 30 of the 100 rubric points: consistency, formatting, tone.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.draft import WEIGHTS

MAX_CONSISTENCY = WEIGHTS["consistency"]  # 15
MAX_FORMATTING = WEIGHTS["formatting"]  # 10
MAX_TONE = WEIGHTS["tone"]  # 5


class JudgeFinding(BaseModel):
    """A subjective defect. Never a blocker — blockers are decided by code."""

    model_config = ConfigDict(frozen=True)

    dimension: str = Field(description="consistency, formatting, or tone")
    message: str = Field(description="what is wrong, in one sentence")
    fix_hint: str = Field(description="what to change, concretely")


class JudgeVerdict(BaseModel):
    """Structured output. Not free-text JSON parsed hopefully."""

    model_config = ConfigDict(frozen=True)

    consistency: int = Field(ge=0, le=MAX_CONSISTENCY)
    formatting: int = Field(ge=0, le=MAX_FORMATTING)
    tone: int = Field(ge=0, le=MAX_TONE)
    findings: list[JudgeFinding] = Field(default_factory=list)
    summary: str = ""

    @property
    def points(self) -> int:
        """0..30, to be added to the deterministic 0..70."""
        return self.consistency + self.formatting + self.tone
