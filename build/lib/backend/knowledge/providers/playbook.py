"""The playbook as a knowledge source.

A deterministic provider, not an agent — and that is the whole point. "Software contracts
get 45-day payment terms" is a policy with a right answer; putting a language model in front
of it would introduce a failure mode where none existed. The rule engine
(`invariants/playbook_rules.py`) evaluates the rules in code, and this provider is the thin
layer that loads a playbook and hands the engine the facts.

## What it evaluates against

The facts the intent establishes: contract_type, country, jurisdiction, industry, language.
Facts a from-scratch draft cannot know yet — a contract value, a negotiated term — are
absent, and a rule whose condition needs an absent fact does not fire. Imposing GDPR on a
contract whose jurisdiction was never determined would be applying an obligation from an
absence of evidence.

## Where the requirements go

Into the CKO's `playbook_rules`, and from there into two places: the transformation planner
(a `require_section` the source does not satisfy is an ADD) and the legal validator (an
unmet blocking requirement refuses finalization). A playbook rule is not advice the drafter
may take or leave — it is a gate.

## Playbooks are read-only

Loaded from `playbooks/`, which is git-versioned and never written by the agent. Per-tenant
playbooks slot in through the same loader once tenancy is threaded through the run context;
until then there is one default playbook, and `_playbook_name` is the single place that
choice is made.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml

from backend.core.logging import get_logger
from backend.core.run_context import RunContext
from backend.invariants.playbook_rules import PlaybookError, Rule, evaluate, load_rules
from backend.knowledge.registry import register_provider
from backend.schemas.cko import Provenance
from backend.schemas.intent import IntentObject
from backend.schemas.provider import KnowledgeContribution

__all__ = ["PLAYBOOK_DIR", "PlaybookProvider", "load_playbook"]

log = get_logger(__name__)

PLAYBOOK_DIR = Path(__file__).resolve().parent.parent.parent.parent / "playbooks"


@cache
def load_playbook(name: str) -> tuple[Rule, ...]:
    """Load and validate a named playbook. Cached — playbooks are read-only git files.

    Refuses a file that is not a playbook, and (via `load_rules`) any rule carrying clause
    text. A malformed playbook fails loudly at load rather than silently contributing
    nothing, because a playbook that silently does nothing is a compliance gap nobody sees.
    """
    path = PLAYBOOK_DIR / f"{name}.yaml"
    if not path.is_file():
        raise PlaybookError(f"no playbook at {path}")
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("rules"), list):
        raise PlaybookError(f"{path} is not a playbook document")
    return load_rules(raw["rules"])


def _playbook_name(ctx: RunContext) -> str:
    """Which playbook this run uses.

    One choice, in one place, so per-tenant playbooks are a change here and nowhere else.
    Today: the default. Tomorrow: keyed on `ctx.tenant_id`.
    """
    return "default"


def _facts(intent: IntentObject) -> dict[str, Any]:
    """The facts a playbook is evaluated against, from the intent.

    Deliberately only what intent establishes. Metadata-derived facts (a contract value)
    are not known at gather time and are left absent rather than guessed.
    """
    return {
        "contract_type": intent.contract_type,
        "country": intent.country,
        "jurisdiction": intent.jurisdiction,
        "industry": intent.industry,
        "language": intent.language,
    }


class PlaybookProvider:
    """Business rules, evaluated in code."""

    name = "playbook"

    async def available(self, intent: IntentObject, ctx: RunContext) -> bool:
        """A playbook exists for this run. The default always does."""
        try:
            return bool(load_playbook(_playbook_name(ctx)))
        except PlaybookError:  # pragma: no cover - a broken default is a deploy problem
            log.warning("playbook %s failed to load", _playbook_name(ctx))
            return False

    async def contribute(self, intent: IntentObject, ctx: RunContext) -> KnowledgeContribution:
        rules = load_playbook(_playbook_name(ctx))
        requirements = evaluate(rules, _facts(intent))
        log.info("playbook=%s fired=%d rules", _playbook_name(ctx), len(requirements))
        return KnowledgeContribution(
            provider=self.name,
            provenance=Provenance(
                provider=self.name,
                locator=f"playbook:{_playbook_name(ctx)}",
            ),
            requirements=requirements,
        )


register_provider(PlaybookProvider())
