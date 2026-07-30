"""The CKO schema will change. Artifacts written before it changed must still be readable.

This is not hypothetical. The plan for this rebuild says outright that the CKO will be
wrong at the point it is designed and that the gap will surface later, when Phase B needs a
fact Phase A never captured. Growing the schema is the expected path, not a failure.

What must not happen is that growing it strands existing artifacts. A run suspended
awaiting user input can be resumed days later, by which time the code may have moved on. If
the CKO written before the deploy no longer parses after it, that run is dead and its work
is lost.

So two properties are pinned here:

- unknown fields are ignored, not rejected (forward compatibility)
- absent fields take their defaults, so an older artifact still loads (backward)
"""

from __future__ import annotations

import json
import uuid

import pytest
from pydantic import ValidationError

from backend.schemas.cko import CKO_SCHEMA_VERSION, ContractKnowledgeObject
from backend.schemas.intent import IntentObject, ResolutionPlan


def _minimal() -> ContractKnowledgeObject:
    return ContractKnowledgeObject(
        contract_id=uuid.uuid4(),
        resolution=ResolutionPlan(providers=("llm",)),
        intent=IntentObject(contract_type="nda", confidence=0.9),
    )


def test_a_new_cko_carries_the_current_version() -> None:
    assert _minimal().schema_version == CKO_SCHEMA_VERSION


def test_an_artifact_from_a_newer_version_still_loads() -> None:
    """Forward compatibility: unknown fields are dropped, not fatal.

    The rollback case. A newer deploy writes a CKO with a field this code has never heard
    of; the deploy is reverted; the older code must still be able to read it. Refusing
    would turn a rollback into data loss.
    """
    payload = json.loads(_minimal().model_dump_json())
    payload["schema_version"] = CKO_SCHEMA_VERSION + 1
    payload["a_field_from_the_future"] = {"nested": ["anything"]}

    loaded = ContractKnowledgeObject.model_validate(payload)

    assert loaded.schema_version == CKO_SCHEMA_VERSION + 1
    assert not hasattr(loaded, "a_field_from_the_future")


def test_an_artifact_from_an_older_version_still_loads() -> None:
    """Backward compatibility: fields added since take their defaults.

    A run suspended awaiting an answer, resumed after a deploy that grew the schema.
    """
    payload = {
        "schema_version": 1,
        "contract_id": str(uuid.uuid4()),
        "resolution": {"providers": ["llm"], "rationale": ""},
        "intent": {"contract_type": "nda", "confidence": 0.9},
    }

    loaded = ContractKnowledgeObject.model_validate(payload)

    assert loaded.intent.contract_type == "nda"
    assert loaded.clause_candidates == ()
    assert loaded.formatting is None
    assert loaded.confidence.overall == 1.0


def test_the_version_is_persisted_not_merely_defaulted() -> None:
    """It has to be *in* the file. A version that only exists in code cannot be read off
    an artifact written by code that is no longer running."""
    assert '"schema_version"' in _minimal().model_dump_json()


def test_structurally_invalid_data_is_still_refused() -> None:
    """`extra="ignore"` must not become "accept anything".

    Tolerating unknown *extra* fields is forward compatibility. Tolerating a missing
    required field, or a wrong type, would be silent corruption.
    """
    with pytest.raises(ValidationError):
        ContractKnowledgeObject.model_validate({"schema_version": 1})

    with pytest.raises(ValidationError):
        ContractKnowledgeObject.model_validate(
            {
                "contract_id": "not-a-uuid",
                "resolution": {"providers": ["llm"]},
                "intent": {"contract_type": "nda", "confidence": 0.9},
            }
        )


def test_confidence_is_bounded() -> None:
    """A confidence of 1.4 is a bug in whatever produced it, and the threshold check that
    decides whether to ask the user would silently never fire."""
    with pytest.raises(ValidationError):
        IntentObject(contract_type="nda", confidence=1.4)


def test_a_cko_is_immutable() -> None:
    """Phase B receives it and must not edit it. A mutable CKO makes the phase boundary
    advisory: drafting could quietly amend what understanding concluded."""
    cko = _minimal()
    with pytest.raises(ValidationError):
        cko.schema_version = 99  # type: ignore[misc]
