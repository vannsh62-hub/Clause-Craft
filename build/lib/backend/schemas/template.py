"""An uploaded DOCX, parsed into structure.

Structure only. Nothing here carries meaning — no clause categories, no risk, no "this
looks like a termination clause". Parsing has a correct answer and is done by code
(`backend/invariants/docx_parse.py`); meaning is a judgement and belongs to the Contract
Understanding Agent. Keeping them apart is what lets the parser be a deterministic,
testable function.

The fidelity requirement in spec 05 §2 lives here. `BlockFingerprint` is what makes it
checkable: two documents have the same paragraph-style structure if their fingerprints
match, regardless of how python-docx happened to reserialise the XML.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BlockFingerprint",
    "FormattingManifest",
    "NumberingScheme",
    "ParsedTable",
    "Placeholder",
    "TemplateObject",
]

BlockKind = Literal["paragraph", "table"]


class BlockFingerprint(BaseModel):
    """Enough of a block's formatting to prove it was not disturbed.

    Deliberately *not* the XML. `python-docx` reserialises the tree on save — attribute
    order, namespace declarations and whitespace all shift even for a load-then-save with
    no edits — so byte comparison of the package is flaky and spec 05 §2's
    "byte-comparable" is unachievable. What is achievable, and is what "paragraph-style
    level fidelity" actually means, is that the style id, the numbering identity and the
    run properties are unchanged.
    """

    model_config = ConfigDict(frozen=True)

    index: int = Field(ge=0)
    kind: BlockKind
    style_id: str | None = None
    #: `w:numPr/w:numId` — the numbering *definition*. A paragraph carrying a list style
    #: but no numId renders unnumbered, which is the single most common way a generated
    #: DOCX looks wrong.
    num_id: int | None = None
    ilvl: int | None = None
    outline_level: int | None = None
    run_props: tuple[str, ...] = ()
    text_sha: str


class Placeholder(BaseModel):
    """A fill-in point found in the source document."""

    model_config = ConfigDict(frozen=True)

    token: str
    block_id: str
    name: str | None = None


class ParsedTable(BaseModel):
    """A table's shape. Cell text lives in the blocks; this is the grid."""

    model_config = ConfigDict(frozen=True)

    block_id: str
    rows: int = Field(ge=0)
    columns: int = Field(ge=0)
    #: (row, column) pairs that are merged. Losing these silently mangles a fee schedule.
    merged: tuple[tuple[int, int], ...] = ()


class NumberingScheme(BaseModel):
    """How the document numbers its sections."""

    model_config = ConfigDict(frozen=True)

    num_ids: tuple[int, ...] = ()
    max_depth: int = Field(default=0, ge=0)
    restarts: bool = False


class FormattingManifest(BaseModel):
    """Everything needed to reproduce untouched sections exactly.

    Present only when a template was resolved. Phase B checks for it: entering the
    drafting engine in template mode without one means the CKO is incomplete, and the
    result would be a regenerated document rather than an edited one.
    """

    model_config = ConfigDict(frozen=True)

    blocks: tuple[BlockFingerprint, ...] = ()
    numbering: NumberingScheme = NumberingScheme()
    tables: tuple[ParsedTable, ...] = ()
    has_headers: bool = False
    has_footers: bool = False
    section_breaks: int = Field(default=0, ge=0)


class TemplateObject(BaseModel):
    """A parsed source document.

    `storage_key` rather than the bytes: `workspace_files.content` is a `Text` column, so
    a 10 MB DOCX cannot live in the virtual filesystem. The binary goes to
    `backend/storage/` and is referenced by key, which also keeps `ls()` cheap.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    storage_key: str
    sha256: str
    size_bytes: int = Field(ge=0)
    filename: str
    formatting: FormattingManifest = FormattingManifest()
    placeholders: tuple[Placeholder, ...] = ()
