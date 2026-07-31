"""Structural checks over a draft's text: placeholders, numbering, cross-references,
definitions, duplicate sections.

Deferred at M2 because it validates drafting output, which did not exist yet. It exists now,
so these are the text-reasoned gates the document and legal validators run. All pure, all
framework-free — a gate with a model inside it is not a gate.

Each function returns `Finding`s rather than raising, so a validator can collect every
problem in one pass and a reviewer sees the whole list, not the first item.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from backend.schemas.draft import Finding

__all__ = [
    "PLACEHOLDER_TOKENS",
    "check_cross_references",
    "check_definitions",
    "check_duplicate_sections",
    "check_numbering",
    "check_placeholders",
    "find_placeholders",
    "headings_of",
]

#: Fragments that mean a value was never filled in. Same list the spec-01 gate used.
PLACEHOLDER_TOKENS: tuple[str, ...] = (
    "{{",
    "}}",
    "[insert",
    "<insert",
    "tbd",
    "xxx",
    "todo",
    "lorem ipsum",
    "[ ]",
)

#: A fill-in-later slot. Three shapes: a mustache `{{ fee }}`, an "insert" instruction
#: `[insert the rent]`, and a bare all-caps bracket `[PROPERTY ADDRESS]` — the last is what a
#: model reaches for by default and what our old detector missed entirely, since it has no
#: "insert" in it. Ordinary bracketed references — a `[1]` citation, an `[a]` sub-clause — are
#: not slots and are deliberately excluded by requiring letters and a run of caps.
_PLACEHOLDER_SPAN = re.compile(
    r"\{\{[^}]*\}\}"  # {{ ... }}
    r"|(?i:\[\s*(?:insert|tbd|to be determined|placeholder)\b[^\]]*\])"  # [insert ...]
    r"|(?i:<\s*insert\b[^>]*>)"  # <insert ...>
    r"|\[[A-Z][A-Z0-9 _/.\-]{3,}\]"  # [PROPERTY ADDRESS] — all caps only
)

_HEADING = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)
#: A leading "1." / "2.3" style number on a heading.
_NUMBERED = re.compile(r"^\s*(\d+)(?:\.\d+)*[.)]?\s")
#: A cross-reference: "§4", "Section 4", "clause 4".
_CROSS_REF = re.compile(r"(?:§\s*|section\s+|clause\s+)(\d+)", re.IGNORECASE)


def headings_of(draft: str) -> list[str]:
    """Every Markdown heading's text, in document order."""
    return _HEADING.findall(draft)


def _finding(dimension: str, severity: str, message: str, fix: str) -> Finding:
    return Finding(dimension=dimension, severity=severity, message=message, fix_hint=fix)


def find_placeholders(draft: str) -> list[str]:
    """Every distinct fill-in-later slot in the draft, in first-seen order.

    A slot is a value the drafter left for a human to complete — `[PROPERTY ADDRESS]`,
    `{{ rent }}`, `[insert the term]`. Returned as text a person can read and go and fill,
    which is what a placeholder is *for*: a marked gap, not an error.
    """
    seen: dict[str, None] = {}
    for match in _PLACEHOLDER_SPAN.finditer(draft):
        seen.setdefault(match.group(0).strip(), None)
    return list(seen)


def check_placeholders(draft: str) -> list[Finding]:
    """Report unfilled placeholders — as a flag, not a blocker.

    A placeholder is a legitimate draft output: the user asked for a contract before they had
    every value, so the draft carries clearly-marked slots they can fill. Blocking on them,
    as this once did, is what made "draft a lease" a dead end instead of a first draft — the
    thing a legal assistant is for.

    So this is `major`, not `blocker`: the draft still finalizes, but it is flagged for review
    and the slots are listed by name. What must *not* happen is a placeholder shipped
    silently, and a finalized-but-flagged draft with the gaps named is the opposite of silent.
    A genuinely broken document — a missing whole section, copied reference text — is a
    different thing and still blocks, in its own check.
    """
    slots = find_placeholders(draft)
    if not slots:
        return []

    shown = ", ".join(slots[:8]) + ("…" if len(slots) > 8 else "")
    count = f"{len(slots)} details are" if len(slots) > 1 else "1 detail is"
    return [
        _finding(
            "placeholders",
            "major",
            f"{count} left as fill-in placeholders: {shown}",
            "these are yours to complete; run the request again with the values to have them "
            "filled in for you",
        )
    ]


def check_numbering(draft: str) -> list[Finding]:
    """Top-level numbered headings must run 1, 2, 3 without a gap or a repeat.

    A jump from 3 to 5, or two 4s, is what a reader notices first and what makes a contract
    look unfinished. Only the top level is checked — nested schemes vary too much to gate.
    """
    numbers = [
        int(m.group(1)) for heading in _HEADING.findall(draft) if (m := _NUMBERED.match(heading))
    ]
    if not numbers:
        return []

    findings: list[Finding] = []
    expected = 1
    seen: set[int] = set()
    for n in numbers:
        if n in seen:
            findings.append(
                _finding(
                    "formatting", "major", f"section {n} is numbered twice", "renumber the sections"
                )
            )
        elif n != expected:
            findings.append(
                _finding(
                    "formatting",
                    "major",
                    f"section numbering jumps to {n} where {expected} was expected",
                    "renumber the sections consecutively",
                )
            )
        seen.add(n)
        expected = max(expected, n) + 1
    return findings


def check_cross_references(draft: str) -> list[Finding]:
    """A "§N" that points past the last section is a dangling reference.

    Cross-references break silently when a section is removed and the reference is not
    updated — the reader is sent to a clause that no longer exists.
    """
    section_count = len(_HEADING.findall(draft))
    findings: list[Finding] = []
    for ref in {int(m) for m in _CROSS_REF.findall(draft)}:
        if ref > section_count:
            findings.append(
                _finding(
                    "consistency",
                    "major",
                    f"a cross-reference points at section {ref}, but there are only "
                    f"{section_count} sections",
                    "fix the reference or restore the section it points at",
                )
            )
    return findings


def check_definitions(draft: str, defined_terms: Iterable[str]) -> list[Finding]:
    """A defined term that never appears in the draft is defined for nothing.

    The tractable half of the definitions gate: `defined and never used`. The other half
    (used but never defined) needs to distinguish a defined term from an ordinary
    capitalised word, which is a judgement, not a check — left to a human reviewer.
    """
    lowered = draft.casefold()
    findings: list[Finding] = []
    for term in defined_terms:
        if term and term.casefold() not in lowered:
            findings.append(
                _finding(
                    "consistency",
                    "minor",
                    f'the term "{term}" is defined but never used',
                    "use the term or remove its definition",
                )
            )
    return findings


def check_duplicate_sections(headings: Iterable[str]) -> list[Finding]:
    """Two sections of a kind that should appear once — two governing-law clauses — conflict.

    A contract with two governing-law clauses does not have a governing law; it has a
    dispute. Checked against a small set of categories that must be unique.
    """
    unique_once = {"governing law", "governing_law", "term and duration", "entire agreement"}
    seen: dict[str, int] = {}
    findings: list[Finding] = []
    for heading in headings:
        key = " ".join(heading.split()).casefold()
        if key in unique_once:
            seen[key] = seen.get(key, 0) + 1
    for key, count in seen.items():
        if count > 1:
            findings.append(
                _finding(
                    "consistency",
                    "blocker",
                    f"the contract has {count} '{key}' sections; there must be one",
                    "merge or remove the duplicate",
                )
            )
    return findings
