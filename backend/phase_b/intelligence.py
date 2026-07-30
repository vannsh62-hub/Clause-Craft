"""The shared Contract Intelligence Engine.

Every UI widget (health score, suggestions, explain, risk heatmap, negotiation mode,
outline, analytics, relationships) is a view over one `ContractAnalysisOut`. This module
is the only place that produces one:

- Clause splitting mirrors `frontend/lib/clauses.ts:parseClauseSections` exactly ("## "
  headings) so clause boundaries are never derived two different ways.
- Deterministic analytics (word count, reading time, readability, unresolved variables)
  are computed in Python and never sent to the model.
- Exactly one AI call (`contract_intelligence_agent`) supplies findings, risk, suggestions,
  explanations and relationships for the *entire* document at once.
- Results are cached in-process by a hash of (document, perspective) so re-fetching the
  same, unchanged contract never re-triggers the model call or re-parses clauses.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass

from backend.core.run_context import RunContext
from backend.core.variable_aliases import resolve_variables
from backend.schemas.intelligence import (
    ClauseAnalysisOut,
    ClauseAnalytics,
    ContractAnalysisOut,
)
from backend.subagents.contract_intelligence.intelligence_agent import analyze_contract

_HEADING_RE = re.compile(r"^##[ \t]+(.*)$", re.MULTILINE)
_HEADING_NUMBER_RE = re.compile(r"^\d+\.[ \t]+")
_VARIABLE_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
_WORDS_PER_MINUTE = 200

#: Cache is per-process, not persisted — acceptable because it's an optimization
#: (avoid re-analysing unchanged documents), never the source of truth.
_CACHE_TTL_SECONDS = 15 * 60
_cache: dict[str, tuple[float, ContractAnalysisOut]] = {}


@dataclass(frozen=True)
class ClauseSection:
    title: str
    heading: str
    start: int
    end: int
    markdown: str

    @property
    def body(self) -> str:
        return self.markdown[len(self.heading) :].strip()


def split_clauses(document: str) -> list[ClauseSection]:
    """Split `document` into '## '-heading clause sections. Mirrors
    `parseClauseSections` in `frontend/lib/clauses.ts` — same rule, same boundaries."""
    if not document:
        return []
    starts = [(m.start(), m.group(1)) for m in _HEADING_RE.finditer(document)]
    sections: list[ClauseSection] = []
    for i, (start, title_raw) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(document)
        line_end = document.find("\n", start)
        heading = document[start : line_end if line_end != -1 else end]
        title = _HEADING_NUMBER_RE.sub("", title_raw).strip()
        sections.append(
            ClauseSection(title=title, heading=heading, start=start, end=end, markdown=document[start:end])
        )
    return sections


def _readability(words: int, sentence_count: int) -> str:
    """Cheap deterministic heuristic (avg words/sentence) — good enough for a UI badge,
    not a claim about a formal readability model, so it never needs the AI call."""
    if sentence_count == 0:
        return "Good"
    avg = words / sentence_count
    if avg <= 22:
        return "Good"
    if avg <= 32:
        return "Fair"
    return "Dense"


def _clause_analytics(section: ClauseSection, cross_ref_count: int, suggestion_count: int) -> ClauseAnalytics:
    body = section.body
    words = len(body.split())
    sentences = max(1, len(re.findall(r"[.!?](?:\s|$)", body)))
    return ClauseAnalytics(
        words=words,
        reading_time_seconds=max(1, round(words / _WORDS_PER_MINUTE * 60)),
        readability=_readability(words, sentences),
        variables=len(set(_VARIABLE_RE.findall(section.markdown))),
        cross_references=cross_ref_count,
        ai_suggestions=suggestion_count,
    )


def _cache_key(document: str, perspective: str) -> str:
    return hashlib.sha256(f"{perspective}:{document}".encode()).hexdigest()


def _cache_get(key: str) -> ContractAnalysisOut | None:
    hit = _cache.get(key)
    if hit is None:
        return None
    ts, value = hit
    if time.monotonic() - ts > _CACHE_TTL_SECONDS:
        _cache.pop(key, None)
        return None
    return value


def invalidate_cache(document: str | None = None, perspective: str | None = None) -> None:
    """Drop one cached analysis, or the whole cache when called with no arguments.
    Called whenever the document changes (edit, insert, remove, fill, variable update)
    so the next `analyze` re-runs instead of silently serving a stale result."""
    if document is None:
        _cache.clear()
        return
    _cache.pop(_cache_key(document, perspective or "neutral"), None)


async def analyze(
    document: str,
    perspective: str,
    contract_variables: dict[str, str],
    ctx: RunContext,
) -> ContractAnalysisOut:
    """Return the shared `ContractAnalysisOut` for `document`, from cache when possible.

    Never analyzes the same document twice for different widgets — call this once per
    document version and let every widget (health card, outline, explain popover, risk
    border, suggestion cards) read its own slice of the same result.
    """
    key = _cache_key(document, perspective)
    cached = _cache_get(key)
    if cached is not None:
        return cached.model_copy(update={"cached": True})

    sections = split_clauses(document)
    titles = [s.title for s in sections]
    known_vars = resolve_variables(contract_variables or {})

    model_out = await analyze_contract(document, titles, perspective, ctx)
    by_title = {c.clause_title: c for c in model_out.clauses}

    clauses_out: list[ClauseAnalysisOut] = []
    for section in sections:
        ai = by_title.get(section.title)
        suggestions = list(ai.suggestions) if ai else []
        cross_refs = list(ai.cross_references) if ai else []
        depends_on = list(ai.depends_on) if ai else []
        referenced_by = list(ai.referenced_by) if ai else []

        unresolved = [
            name for name in set(_VARIABLE_RE.findall(section.markdown)) if not known_vars.get(name)
        ]

        clauses_out.append(
            ClauseAnalysisOut(
                id=section.title.lower().replace(" ", "-"),
                title=section.title,
                risk=ai.risk if ai else "medium",
                risk_reason=ai.risk_reason if ai else "",
                summary=ai.summary if ai else "",
                plain_english=ai.plain_english if ai else "",
                business_purpose=ai.business_purpose if ai else "",
                negotiation_tips=ai.negotiation_tips if ai else "",
                common_alternatives=ai.common_alternatives if ai else "",
                potential_problems=ai.potential_problems if ai else "",
                suggestions=suggestions,
                depends_on=depends_on,
                referenced_by=referenced_by,
                cross_references=cross_refs,
                analytics=_clause_analytics(section, len(cross_refs), len(suggestions)),
                variables_unresolved=unresolved,
            )
        )

    ok_count = sum(1 for f in model_out.findings if f.ok)
    total = max(1, len(model_out.findings))
    penalty = 6 * len(model_out.missing_clauses)
    health_score = max(0, min(100, round(100 * ok_count / total) - penalty))

    result = ContractAnalysisOut(
        health_score=health_score,
        findings=list(model_out.findings),
        missing_clauses=list(model_out.missing_clauses),
        clauses=clauses_out,
        cached=False,
    )
    _cache[key] = (time.monotonic(), result)
    return result
