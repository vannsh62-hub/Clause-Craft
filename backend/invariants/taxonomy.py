"""The clause taxonomy, and conformance to it.

`ClauseCandidate.category` is the single highest-leverage field in the whole Contract
Knowledge Object. Clause recommendation, risk analysis, playbook validation and automatic
clause-library construction all read it and little else. That only works if the vocabulary
is shared — two contracts that both have a confidentiality clause must agree on what to
call it, or nothing downstream can compare them.

So the category is drawn from a fixed list, and conformance is checked rather than
requested. A model asked to "use one of these categories" will occasionally produce a
plausible near-miss — `confidential_information` for `confidentiality`, `ip` for
`intellectual_property` — and a near-miss is worse than an obvious error, because it looks
right in the artifact and silently fails to match anywhere downstream.

Free-form nuance belongs in `subcategory`, which constrains nothing.

Framework-free by design and by test: this is a gate, and a gate with a model inside it is
not a gate.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from backend.schemas.cko import ClauseCandidate
from backend.schemas.errors import ContractToolError

__all__ = [
    "TAXONOMY_PATH",
    "TaxonomyError",
    "categories",
    "describe",
    "is_known",
    "unknown_categories",
]

TAXONOMY_PATH = (
    Path(__file__).resolve().parent.parent.parent / "skills" / "clause-taxonomy" / "taxonomy.yaml"
)


class TaxonomyError(ContractToolError):
    """A clause category outside the shared vocabulary."""


class _Category(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    description: str = ""


@cache
def _load() -> tuple[_Category, ...]:
    raw: Any = yaml.safe_load(TAXONOMY_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("categories"), list):
        raise TaxonomyError(f"{TAXONOMY_PATH} is not a taxonomy document")

    parsed = tuple(_Category.model_validate(entry) for entry in raw["categories"])
    ids = [c.id for c in parsed]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise TaxonomyError(f"duplicate taxonomy ids: {sorted(duplicates)}")
    return parsed


@cache
def categories() -> tuple[str, ...]:
    """Every valid category id, in file order.

    File order rather than alphabetical: the file is grouped by what the clauses are *for*
    (money, time, risk, machinery), and that grouping is useful in a prompt.
    """
    return tuple(c.id for c in _load())


def describe(category: str) -> str:
    return next((c.description for c in _load() if c.id == category), "")


def is_known(category: str) -> bool:
    return category in categories()


def unknown_categories(candidates: tuple[ClauseCandidate, ...]) -> tuple[str, ...]:
    """Categories used by `candidates` that are not in the taxonomy.

    Returns them rather than raising, so a caller can decide: the classifier retries once,
    while validation blocks. Sorted and de-duplicated — a hundred clauses in one bad
    category is one problem, not a hundred.
    """
    return tuple(sorted({c.category for c in candidates if not is_known(c.category)}))


def taxonomy_prompt_block() -> str:
    """The vocabulary, formatted for inclusion in a prompt.

    Built from the file rather than duplicated into the prompt text, so the list a model is
    shown and the list it is validated against cannot drift apart.
    """
    return "\n".join(f"- `{c.id}` — {c.description.strip()}" for c in _load())
