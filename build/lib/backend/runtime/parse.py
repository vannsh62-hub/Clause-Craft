"""Normalising structured output across runtimes.

The two runtimes disagree about what "structured output" returns, and the disagreement is
exactly the sort of thing that leaks into call sites and quietly welds the codebase to one
engine:

    openai-agents   `output_type=Model` -> `result.final_output_as(Model)`, a real instance
    deepagents      `response_format=Model` -> a JSON *string* inside a ToolMessage

So the contract is: **the adapter produces a candidate payload; this module decides
whether it is valid.** Adapters never validate, and callers never see a raw payload. A
new adapter satisfies the contract by handing whatever its engine returns to
`coerce_output`, and `tests/test_runtime_port.py` pins all four shapes today — long before
there is a second adapter to break.

Models fenced their JSON in ```` ```json ```` long before either library existed, and they
still do it intermittently under a schema constraint. Stripping fences here rather than in
each adapter means one place gets it right.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ValidationError

from backend.runtime.spec import AgentSpec, Out
from backend.schemas.errors import ContractToolError

__all__ = ["MalformedAgentOutput", "coerce_output", "strip_fences"]


class MalformedAgentOutput(ContractToolError):
    """A sub-agent returned something its spec cannot accept.

    A `ContractToolError` on purpose: the orchestrator decides what to do about a
    malformed sub-agent — usually retry once with a narrower instruction — rather than
    the whole run dying. This mirrors how `run_subagent` already translates the SDK's
    `ModelBehaviorError`.
    """


def strip_fences(raw: str) -> str:
    """Remove a Markdown code fence around a JSON payload, if present."""
    text = raw.strip()
    if not text.startswith("```"):
        return text
    body = text[3:]
    newline = body.find("\n")
    if newline == -1:
        return text
    # Drop an optional language tag on the opening fence ("json", "JSON", "").
    if body[:newline].strip().lower() not in {"", "json"}:
        return text
    body = body[newline + 1 :]
    closing = body.rfind("```")
    return (body[:closing] if closing != -1 else body).strip()


def coerce_output(spec: AgentSpec[Out], raw: object) -> Out:
    """Validate `raw` against the spec's declared output model.

    Accepts the shapes the supported runtimes actually produce: an already-validated
    instance, a JSON string (fenced or not), or a mapping.
    """
    model = spec.output_model
    if model is None:
        raise MalformedAgentOutput(
            f"{spec.name} declares no output_model, so it has no structured output to read."
        )

    candidate: Any = raw
    if isinstance(candidate, model):
        return candidate

    try:
        if isinstance(candidate, str):
            return model.model_validate_json(strip_fences(candidate))
        if isinstance(candidate, BaseModel):
            # A different model class: round-trip through a dict rather than guessing.
            candidate = candidate.model_dump()
        if isinstance(candidate, dict):
            return model.model_validate(candidate)
    except (ValidationError, json.JSONDecodeError) as exc:
        raise MalformedAgentOutput(
            f"{spec.name} returned output that does not match {model.__name__}; retry once."
        ) from exc

    raise MalformedAgentOutput(
        f"{spec.name} returned {type(raw).__name__}, which cannot be read as "
        f"{model.__name__}; retry once."
    )
