"""A contract as a document, rather than as a blob of Markdown.

The engine could always produce correct *sentences*; what it could not produce was a
*contract*. A draft that opens `## Scope of Services` and stops after the last clause is
missing every structural convention a legal document has — a title, a party block, recitals,
numbered operative clauses, an execution page — and reads as generic no matter how good the
prose is.

Those conventions are structure, not language, so they belong in a schema and a deterministic
renderer rather than in a prompt that asks a model to remember them. This module is that
schema. `backend/invariants/docx_build.py` renders it to DOCX and to text, and the two are
derived from this one object so the document that is validated is the document that ships.

## What is numbered, and what is not

Only `clauses` are numbered. The preamble, the recitals and the signature block are
deliberately unnumbered — numbering them is the single most recognisable way to make a
contract look machine-generated. Numbering is applied by Word from the tree's depth; no
number is ever written into `heading` or `text`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

__all__ = ["ClauseNode", "ContractDocument", "Signatory"]


class ClauseNode(BaseModel):
    """One operative clause, and its sub-clauses.

    Recursive because contract numbering is: clause 1 has 1.1, which has (a). Depth in this
    tree *is* the numbering level, which is why no caller ever writes "1.1" anywhere — the
    renderer derives it, and a renumber is a tree operation rather than a search and replace.
    """

    model_config = ConfigDict(frozen=True)

    heading: str
    text: str = ""
    children: tuple[ClauseNode, ...] = ()


class Signatory(BaseModel):
    """A party as they appear on the execution page."""

    model_config = ConfigDict(frozen=True)

    name: str
    role: str = ""


class ContractDocument(BaseModel):
    """A complete contract, in the order the parts appear on the page.

    Every field except `clauses` exists because a real contract has it and our drafts did
    not. `schema_version` is here for the same reason it is on the CKO: this shape will be
    wrong in some detail, and finding that out must not be a migration event.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: int = 1

    #: "SERVICES AGREEMENT". Set once, and never repeated as the first clause heading.
    title: str

    #: "This Agreement is made on 1 August 2026 between A (…) and B (…)." Unnumbered.
    preamble: str = ""

    #: WHEREAS-style context. Unnumbered, and omitted entirely when there is none to give.
    recitals: tuple[str, ...] = ()

    #: The operative provisions — the only numbered part of the document.
    clauses: tuple[ClauseNode, ...] = ()

    #: "IN WITNESS WHEREOF the parties have executed this Agreement…"
    execution_note: str = ""

    #: Who signs. Rendered on a fresh page with By / Name / Title / Date lines each.
    signatories: tuple[Signatory, ...] = ()
