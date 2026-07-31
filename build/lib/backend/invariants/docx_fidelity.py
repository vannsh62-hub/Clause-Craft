"""Proving that an edit disturbed only what it was supposed to disturb.

The guarantee a user actually wants when they upload a DOCX is: *the parts I did not ask
you to change come back exactly as they were.* This module is how that is checked.

It is not byte equality of the package. `python-docx` reserialises the XML tree on save —
attribute ordering, namespace declarations and whitespace all move even on a load-then-save
with no edits — so a byte comparison of the whole document is flaky and would be turned off
within a week. Spec 05 §2 asks for "byte-comparable at the paragraph-style level"; the
achievable and meaningful version of that is:

1. every KEEP block's fingerprint is unchanged, and
2. the parts that define what styles *mean* — `styles.xml`, `numbering.xml` — are byte
   identical, because those are shared and a change to one silently restyles the document.

Together those catch the failures that matter: a lost `w:numPr`, a restyled heading, a
dropped table row, a body reflowed by regeneration.
"""

from __future__ import annotations

import zipfile
from io import BytesIO

from pydantic import BaseModel, ConfigDict

from backend.invariants.docx_parse import block_ids, style_fingerprint
from backend.schemas.template import BlockFingerprint

__all__ = ["FidelityReport", "SHARED_PARTS", "compare", "shared_parts_match"]

#: Parts that define what styles and numbering *mean*. A change here restyles blocks whose
#: own properties never changed, so fingerprint equality alone would miss it.
SHARED_PARTS = ("word/styles.xml", "word/numbering.xml")


class FidelityReport(BaseModel):
    """What changed between two versions of a document."""

    model_config = ConfigDict(frozen=True)

    changed_blocks: tuple[str, ...] = ()
    removed_blocks: tuple[str, ...] = ()
    added_blocks: tuple[str, ...] = ()
    changed_parts: tuple[str, ...] = ()

    @property
    def unchanged(self) -> bool:
        return not (
            self.changed_blocks or self.removed_blocks or self.added_blocks or self.changed_parts
        )


def _by_id(fingerprints: tuple[BlockFingerprint, ...]) -> dict[str, BlockFingerprint]:
    return dict(zip(block_ids(fingerprints), fingerprints, strict=True))


def _formatting_of(fingerprint: BlockFingerprint) -> tuple[object, ...]:
    """Everything except the text.

    Text is excluded on purpose: a MODIFY block is *supposed* to have new words, and
    including text here would report every intended edit as a fidelity failure. What must
    not change is how it is styled and numbered.
    """
    return (
        fingerprint.kind,
        fingerprint.style_id,
        fingerprint.num_id,
        fingerprint.ilvl,
        fingerprint.outline_level,
        fingerprint.run_props,
    )


def shared_parts_match(before: bytes, after: bytes) -> tuple[str, ...]:
    """Return the shared parts that differ. Empty means the styling contract held."""
    changed: list[str] = []
    with zipfile.ZipFile(BytesIO(before)) as a, zipfile.ZipFile(BytesIO(after)) as b:
        names_a, names_b = set(a.namelist()), set(b.namelist())
        for part in SHARED_PARTS:
            if part not in names_a and part not in names_b:
                continue
            if (part in names_a) != (part in names_b):
                changed.append(part)
                continue
            if a.read(part) != b.read(part):
                changed.append(part)
    return tuple(changed)


def compare(
    before: bytes, after: bytes, *, expect_unchanged: tuple[str, ...] = ()
) -> FidelityReport:
    """Compare two documents.

    `expect_unchanged` are the block ids that must be untouched — the KEEP set. When it is
    supplied, only those blocks are reported on, which is what makes this usable after a
    real edit: the MODIFY and ADD blocks are meant to differ, and reporting them would
    bury the one finding that matters.
    """
    old = _by_id(style_fingerprint(before))
    new = _by_id(style_fingerprint(after))

    watched = set(expect_unchanged) if expect_unchanged else set(old)

    changed = [
        identity
        for identity in sorted(watched & set(new))
        if _formatting_of(old[identity]) != _formatting_of(new[identity])
        or (not expect_unchanged and old[identity].text_sha != new[identity].text_sha)
    ]
    removed = sorted(watched - set(new))
    added = sorted(set(new) - set(old)) if not expect_unchanged else []

    return FidelityReport(
        changed_blocks=tuple(changed),
        removed_blocks=tuple(removed),
        added_blocks=tuple(added),
        changed_parts=shared_parts_match(before, after),
    )
