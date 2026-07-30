"""Fake models for driving the whole pipeline in a test, without a live API.

The pipeline calls several agents, each reading its model from a module-level `RUNTIME` seam.
`wire_pipeline` replaces all of them at once with scripted `FakeModel`s, so a test can drive
a real run through the ASGI app and background task while spending nothing.

Pass one confidence per pipeline run the test will trigger. A start that suspends and a resume
that completes is *two* runs, so `confidences=(0.3, 0.95)` makes the intent model report low
confidence first (the run asks and suspends) and high confidence on the resume (it drafts).
Only the successful run reaches planning and drafting, so those need one script regardless.
"""

from __future__ import annotations

import json

import pytest

from backend.phase_a import intent as intent_mod
from backend.phase_b import drafting as drafting_mod
from backend.phase_b import planning as planning_mod
from backend.runtime.adapters.openai_agents import OpenAIAgentsRuntime
from tests.fakes import FakeModel, Turn, text_message

__all__ = ["wire_pipeline"]

_INTENT = {
    "contract_type": "nda",
    "parties": [{"name": "ABC", "role": "Disclosing Party"}],
    "jurisdiction": "IN",
    "purpose": "protect disclosures",
    "mode": "ai_drafting",
    "confidence": 0.95,
    "needs_clarification": [],
}

_DRAFT_PLAN = {
    "sections": [
        {"name": "Confidentiality", "order": 0, "rationale": "core", "source": "llm"},
        {"name": "Data Protection", "order": 1, "rationale": "DPDP", "source": "playbook"},
        {"name": "Governing Law", "order": 2, "rationale": "required", "source": "playbook"},
    ]
}

_TRANSFORMATION = {
    "add": [
        {"name": "Confidentiality", "decision": "add", "reason": "core NDA clause"},
        {"name": "Data Protection", "decision": "add", "reason": "playbook: DPDP"},
        {"name": "Governing Law", "decision": "add", "reason": "playbook: governing law"},
    ]
}

_DRAFTED = {
    "sections": [
        {"ref": "Confidentiality", "text": "The receiving party shall keep it secret."},
        {"ref": "Data Protection", "text": "Personal data is handled under the DPDP Act."},
        {"ref": "Governing Law", "text": "Governed by the laws of India."},
    ]
}


def _runtime(*payloads: dict) -> OpenAIAgentsRuntime:
    return OpenAIAgentsRuntime(
        FakeModel([Turn(output=[text_message(json.dumps(p))]) for p in payloads])
    )


def wire_pipeline(
    monkeypatch: pytest.MonkeyPatch, *, confidences: tuple[float, ...] = (0.95,)
) -> None:
    """Fake every model the pipeline calls, in Mode 1 (no template).

    `confidences` is one intent payload per pipeline run the test will drive.
    """
    intents = [{**_INTENT, "confidence": c} for c in confidences]
    monkeypatch.setattr(intent_mod, "RUNTIME", _runtime(*intents))
    monkeypatch.setattr(planning_mod, "RUNTIME", _runtime(_DRAFT_PLAN, _TRANSFORMATION))
    monkeypatch.setattr(drafting_mod, "RUNTIME", _runtime(_DRAFTED))
