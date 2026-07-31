"""Phase B's plans: what sections should exist, and what happens to each.

`TransformationPlan` is the pivot of the whole system. It is the difference between
*generating* a contract and *transforming* one, and it exists as a file on disk before any
text is written so that "why was the arbitration clause removed?" is answered by reading an
artifact rather than by re-running a model.

Drafting is blocked until this artifact exists — enforced by the tool signature and the
invariant layer, not by a prompt.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.cko import SourceRef

__all__ = [
    "Decision",
    "DraftPlan",
    "DraftedContent",
    "DraftedSection",
    "PlannedSection",
    "SectionDecision",
    "TransformationPlan",
]

#: Where a planned section's content comes from.
SectionSource = Literal["llm", "template", "library", "playbook"]

Decision = Literal["keep", "modify", "remove", "add"]


class PlannedSection(BaseModel):
    """One section the draft should contain.

    `rationale` is not decoration. A plan whose entries cannot be justified is a plan
    nobody can review, and reviewability is the point of planning before writing.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    order: int = Field(ge=0)
    rationale: str
    source: SectionSource


class DraftPlan(BaseModel):
    """The intended shape of the document, decided before any text exists."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    sections: tuple[PlannedSection, ...] = ()


class SectionDecision(BaseModel):
    """What happens to one section, and why.

    `source_ref` points at the block this decision applies to, which is how the DOCX
    editor knows what to leave alone. A KEEP decision with no `source_ref` cannot be
    honoured — there is nothing to keep — so the editor treats it as an error rather than
    regenerating the section.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    decision: Decision
    reason: str
    source_ref: SourceRef | None = None


class DraftedSection(BaseModel):
    """Text the drafting agent produced for one MODIFY or ADD decision.

    `ref` is the key the DOCX editor places it by: a MODIFY carries the source block's id,
    an ADD carries the section name (it has no source block). The drafting agent is told
    exactly which ref to use for each slot, because a mismatched ref means the text is
    silently dropped and the section comes out empty.
    """

    model_config = ConfigDict(frozen=True)

    ref: str
    heading: str = ""
    text: str


class DraftedContent(BaseModel):
    """Everything the drafting agent wrote: one entry per section it had to fill.

    KEEP and REMOVE sections are absent — a KEEP is not rewritten and a REMOVE is not
    written at all. Only MODIFY and ADD produce text.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    sections: tuple[DraftedSection, ...] = ()

    #: Unnumbered WHEREAS-style context, for generation mode. The rest of the document's
    #: envelope — title, party block, signature page — is derived from facts in the CKO
    #: rather than written, so this is the only part of it the agent supplies.
    recitals: tuple[str, ...] = ()


class TransformationPlan(BaseModel):
    """Every section classified before a word is written.

    Deliberately four explicit lists rather than one list with a `decision` field: the
    shape makes "what is being removed?" a lookup rather than a filter, and makes an
    empty `remove` visibly empty in the persisted JSON.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    keep: tuple[SectionDecision, ...] = ()
    modify: tuple[SectionDecision, ...] = ()
    remove: tuple[SectionDecision, ...] = ()
    add: tuple[SectionDecision, ...] = ()

    @property
    def all_decisions(self) -> tuple[SectionDecision, ...]:
        return self.keep + self.modify + self.remove + self.add

    @property
    def touches_everything(self) -> bool:
        """True when nothing is preserved.

        Worth checking before drafting: a plan that keeps nothing is a regeneration
        wearing a transformation's clothes, and in template mode that silently discards
        the formatting the user uploaded the document for.
        """
        return not self.keep and bool(self.all_decisions)
