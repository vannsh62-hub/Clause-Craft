from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Dimension = Literal["completeness", "placeholders", "fidelity", "consistency", "formatting", "tone"]
Severity = Literal["blocker", "major", "minor"]

# The rubric. A number without weights is theatre.
#
# The first three are decided by code, in milliseconds, for zero tokens — and they carry
# 70 of the 100 points because they catch the defects that actually void a contract.
# The last three are genuinely subjective and are scored by the LLM judge (M6).
WEIGHTS: dict[Dimension, int] = {
    "completeness": 30,
    "placeholders": 25,
    "fidelity": 15,
    "consistency": 15,
    "formatting": 10,
    "tone": 5,
}

DETERMINISTIC_DIMENSIONS: tuple[Dimension, ...] = ("completeness", "placeholders", "fidelity")
DETERMINISTIC_MAX = sum(WEIGHTS[d] for d in DETERMINISTIC_DIMENSIONS)  # 70
JUDGE_MAX = 100 - DETERMINISTIC_MAX  # 30

#: A draft carrying any blocker cannot reach the pass mark, whatever else is perfect.
BLOCKED_SCORE_CEILING = 89


class Finding(BaseModel):
    model_config = ConfigDict(frozen=True)

    dimension: Dimension
    severity: Severity
    message: str
    fix_hint: str
    clause_id: str | None = None


class ValidationReport(BaseModel):
    """The output of the deterministic gates. No model contributed to this."""

    model_config = ConfigDict(frozen=True)

    deterministic_points: int = Field(ge=0, le=DETERMINISTIC_MAX)
    findings: tuple[Finding, ...] = ()

    present_clause_ids: tuple[str, ...] = ()
    missing_required_ids: tuple[str, ...] = ()
    altered_clause_ids: tuple[str, ...] = ()

    @property
    def blockers(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == "blocker")

    @property
    def ok(self) -> bool:
        """True when nothing disqualifying was found. Not the same as 'scores well'."""
        return not self.blockers
