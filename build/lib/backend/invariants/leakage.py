"""Detecting reference text that has been copied into a draft.

A reference document is a source of **knowledge**, never of words. The system may learn
that a previous vendor NDA used a mutual confidentiality obligation; it may not reproduce
that NDA's sentences. Copying is a licensing problem, a confidentiality problem, and — when
the reference belongs to a different counterparty — a disclosure problem.

The primary defence is structural: `KnowledgeGraph` has no field that can carry clause
text, and Phase B never receives raw reference documents, so the drafting agent cannot copy
what it cannot see. This module is the check that the structural defence held. Two layers,
because the expensive failure is silent.

**Why n-grams rather than similarity.** A similarity ratio between a reference document and
a draft is meaningless: both are contracts, both are long, and both share the boilerplate
every contract shares. What matters is whether a *run* of distinctive words appears in
both. Eight words is long enough that ordinary legal phrasing does not trip it and short
enough to catch a copied sentence fragment.

**Why not semantic paraphrase detection.** It is the thorough version and it is a
false-positive machine on boilerplate — "governing law" clauses are near-identical across
every contract ever written, and flagging them would train reviewers to ignore the gate.
Start with the cheap, deterministic, explainable check; measure before adding more.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from pydantic import BaseModel, ConfigDict

__all__ = ["LeakHit", "NGRAM_WORDS", "find_leaks", "ngrams"]

#: Words per shard. Below about six, ordinary legal phrasing collides ("the parties agree
#: that the receiving party shall"); well above eight, a copied sentence that has been
#: lightly edited slips through. Eight is the compromise, chosen to be explainable rather
#: than tuned.
NGRAM_WORDS = 8

#: Report only runs at least this long.
#:
#: Detection and reporting are separate thresholds on purpose. Eight words is the right
#: window for *noticing* a match, but eight words of pure boilerplate genuinely do collide
#: — "this agreement shall be governed by the laws" is common to two contracts that share
#: nothing else. Requiring a longer contiguous run to report means incidental legal
#: phrasing is ignored while a copied sentence is not.
#:
#: This is the same shape as `_MIN_MATCH_RUN` in `validate.py`, for the same reason: it is
#: the difference between a gate that works and a gate that merely appears to. An
#: allow-list of known boilerplate was the alternative, and every entry in such a list is
#: a hole someone can drive a copied clause through.
MIN_RUN_WORDS = 12


def _normalise(text: str) -> str:
    """Collapse whitespace and case.

    Deliberately the same normalisation as the fidelity gate in `validate.py`: reflowing a
    paragraph is not a copy, and casing is not a defence.
    """
    return " ".join(text.split()).casefold()


def ngrams(text: str, size: int = NGRAM_WORDS) -> set[str]:
    """Every `size`-word shard of `text`, normalised."""
    words = _normalise(text).split()
    if len(words) < size:
        return set()
    return {" ".join(words[i : i + size]) for i in range(len(words) - size + 1)}


class LeakHit(BaseModel):
    """One passage that appears in both a reference document and the output."""

    model_config = ConfigDict(frozen=True)

    document: str
    passage: str

    def __str__(self) -> str:  # pragma: no cover - convenience for findings text
        return f"{self.document}: {self.passage!r}"


def find_leaks(
    output: str,
    references: Iterable[tuple[str, str]],
    *,
    size: int = NGRAM_WORDS,
    min_run: int = MIN_RUN_WORDS,
) -> tuple[LeakHit, ...]:
    """Return passages from `references` that appear verbatim in `output`.

    `references` is `(document name, text)` pairs. An empty result means the structural
    defence held; a non-empty one is a blocker, not a warning — the draft contains someone
    else's words.

    Works over the *output's* word positions rather than over a set of matched shards.
    A copied sentence produces many overlapping shards describing the same text, and a set
    has no order to merge them by; marking covered positions and then reading off maximal
    runs gives one finding per copied passage, in document order, naming text a human can
    go and look at.
    """
    words = _normalise(output).split()
    if len(words) < size:
        return ()

    hits: list[LeakHit] = []
    for name, text in references:
        shards = ngrams(text, size)
        if not shards:
            continue

        covered = [False] * len(words)
        for start in range(len(words) - size + 1):
            if " ".join(words[start : start + size]) in shards:
                covered[start : start + size] = [True] * size

        hits.extend(
            LeakHit(document=name, passage=" ".join(words[start:end]))
            for start, end in _runs(covered)
            if end - start >= min_run
        )
    return tuple(hits)


def _runs(covered: Sequence[bool]) -> list[tuple[int, int]]:
    """Maximal `[start, end)` spans of consecutive True."""
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for index, flag in enumerate(covered):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            spans.append((start, index))
            start = None
    if start is not None:
        spans.append((start, len(covered)))
    return spans
