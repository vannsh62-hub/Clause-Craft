"""Reading and editing the playbook.

A **playbook** is the set of business rules a contract must satisfy — the standing policy of
whoever owns the contracts. "Software deals get 45-day payment terms." "Anything touching EU
personal data must have a data-protection section." "Contracts over ten million need legal
sign-off." Each is a rule: *when* some facts hold, *require* (or forbid, or set, or flag)
something. The drafting engine reads these and enforces them, so a rule here is a guardrail,
not a suggestion.

This router exposes the rules two ways: as raw YAML (`GET`/`PUT /playbook`, for power users)
and as structured rows (`/playbook/rules`, which the UI shows as a table and edits one rule
at a time). Both write the same file, and every write is validated through the same loader
the pipeline uses — a rule that will not load, or that smuggles clause text where a condition
belongs, is refused with the reason.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.invariants.playbook_rules import PlaybookError, load_rules
from backend.knowledge.providers.playbook import PLAYBOOK_DIR, load_playbook

router = APIRouter(prefix="/playbook", tags=["playbook"])

_NAME = "default"

#: Prepended when the file is rewritten structurally, so it stays self-documenting even after
#: the UI reserialises it (which drops any hand-written comments).
_HEADER = (
    "# The playbook: business rules a contract must satisfy.\n"
    "#\n"
    "# Each rule says: WHEN some facts hold, REQUIRE / FORBID / SET / FLAG something.\n"
    "# A rule states a condition — the contract language lives in the clause library, not\n"
    "# here. Edited through the app; validated on every save.\n\n"
)

_EXPLANATION = (
    "A playbook is your standing policy for contracts. Each rule fires when its conditions "
    "hold and then requires a section, forbids one, sets a value, or raises a flag. The "
    "drafting engine enforces these, so they are guardrails, not suggestions."
)


# --------------------------------------------------------------------------- raw YAML view


class PlaybookBody(BaseModel):
    yaml: str


class PlaybookView(BaseModel):
    yaml: str
    rule_count: int


def _path() -> Path:
    return PLAYBOOK_DIR / f"{_NAME}.yaml"


@router.get("", response_model=PlaybookView)
async def get_playbook() -> PlaybookView:
    """The current playbook, as YAML, plus how many rules it holds."""
    text = _path().read_text(encoding="utf-8")
    return PlaybookView(yaml=text, rule_count=len(load_playbook(_NAME)))


@router.put("", response_model=PlaybookView)
async def put_playbook(body: PlaybookBody) -> PlaybookView:
    """Validate and save an edited playbook (raw YAML)."""
    rules = _validate_yaml(body.yaml)
    _path().write_text(body.yaml, encoding="utf-8")
    load_playbook.cache_clear()
    return PlaybookView(yaml=body.yaml, rule_count=len(rules))


# ----------------------------------------------------------------------- structured rules


class ConditionIn(BaseModel):
    field: str
    op: str
    value: Any = None


class RuleIn(BaseModel):
    id: str
    when: list[ConditionIn] = []
    kind: str
    target: str
    value: str | None = None
    reason: str = ""
    blocking: bool = True


class RulesView(BaseModel):
    rules: list[dict[str, Any]]
    explanation: str


@router.get("/rules", response_model=RulesView)
async def get_rules() -> RulesView:
    """The rules as structured rows, for the table view."""
    return RulesView(rules=_read_rules(), explanation=_EXPLANATION)


@router.post("/rules", response_model=RulesView, status_code=201)
async def add_rule(rule: RuleIn) -> RulesView:
    """Add a rule."""
    rules = _read_rules()
    if any(r.get("id") == rule.id for r in rules):
        raise HTTPException(status_code=409, detail=f"a rule with id {rule.id!r} already exists")
    rules.append(_to_dict(rule))
    _write_rules(rules)
    return RulesView(rules=rules, explanation=_EXPLANATION)


@router.put("/rules/{rule_id}", response_model=RulesView)
async def edit_rule(rule_id: str, rule: RuleIn) -> RulesView:
    """Replace a rule by id."""
    rules = _read_rules()
    index = next((i for i, r in enumerate(rules) if r.get("id") == rule_id), None)
    if index is None:
        raise HTTPException(status_code=404, detail=f"no rule with id {rule_id!r}")
    rules[index] = _to_dict(rule)
    _write_rules(rules)
    return RulesView(rules=rules, explanation=_EXPLANATION)


@router.delete("/rules/{rule_id}", response_model=RulesView)
async def remove_rule(rule_id: str) -> RulesView:
    """Remove a rule by id."""
    rules = _read_rules()
    remaining = [r for r in rules if r.get("id") != rule_id]
    if len(remaining) == len(rules):
        raise HTTPException(status_code=404, detail=f"no rule with id {rule_id!r}")
    _write_rules(remaining)
    return RulesView(rules=remaining, explanation=_EXPLANATION)


# ------------------------------------------------------------------------------- helpers


def _validate_yaml(text: str) -> tuple[Any, ...]:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=422, detail=f"invalid YAML: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("rules"), list):
        raise HTTPException(
            status_code=422,
            detail="a playbook must be a document with a top-level 'rules' list",
        )
    try:
        return load_rules(raw["rules"])
    except PlaybookError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _read_rules() -> list[dict[str, Any]]:
    raw = yaml.safe_load(_path().read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("rules"), list):
        raise HTTPException(status_code=500, detail="the playbook file is malformed")
    return list(raw["rules"])


def _to_dict(rule: RuleIn) -> dict[str, Any]:
    """A rule row as a clean dict for YAML, dropping empties."""
    out: dict[str, Any] = {"id": rule.id, "kind": rule.kind, "target": rule.target}
    out["when"] = [
        (
            {"field": c.field, "op": c.op, "value": c.value}
            if c.value is not None
            else {"field": c.field, "op": c.op}
        )
        for c in rule.when
    ]
    if rule.value is not None and rule.value != "":
        out["value"] = rule.value
    if rule.reason:
        out["reason"] = rule.reason
    out["blocking"] = rule.blocking
    return out


def _write_rules(rules: list[dict[str, Any]]) -> None:
    """Validate the rule set, then rewrite the file with the documentation header."""
    try:
        load_rules(rules)
    except PlaybookError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    body = yaml.safe_dump({"rules": rules}, sort_keys=False, default_flow_style=False)
    _path().write_text(_HEADER + body, encoding="utf-8")
    load_playbook.cache_clear()
