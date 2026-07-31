"""The report the legal and document validators produce.

Distinct from `schemas/draft.py::ValidationReport`, which is spec 01's *scoring* report (70
deterministic points plus a judge's 30). That one answers "how good is this draft?"; this
one answers "may this draft become a document at all?". Keeping them separate avoids
overloading one type with two jobs — a scoring report that also gated finalization would
tangle "below the pass mark" (ship it, flag it) with "has a blocker" (refuse), which are
opposite outcomes.

A `blocker` finding refuses finalization. `major` and `minor` are recorded and surfaced but
do not block — the system never silently withholds a document, it withholds only one that
would be void.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from backend.schemas.draft import Finding

__all__ = ["GateKind", "GateReport"]

GateKind = Literal["legal", "document"]


class GateReport(BaseModel):
    """The findings from one validator.

    `kind` says which validator produced it — a numbering gap and a missing indemnity have
    nothing in common, so they are reported by different validators and a reviewer can tell
    at a glance which kind of problem they are looking at.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    kind: GateKind
    findings: tuple[Finding, ...] = ()

    @property
    def blockers(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == "blocker")

    @property
    def passed(self) -> bool:
        """No blocker. Not "no findings" — a `major` is worth surfacing but does not void
        the contract, so it does not refuse finalization."""
        return not self.blockers
