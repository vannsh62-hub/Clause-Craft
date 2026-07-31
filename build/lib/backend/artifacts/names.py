"""Every artifact a run produces: its path and its type, in one place.

This layout **is** the explainability feature. "Why was the arbitration clause removed?"
is answered by reading `work/06-transformation-plan.json`; "why this indemnity wording?"
by `work/07-clause-recommendations.json`. Neither requires re-running a model, and neither
depends on anyone having thought to log the right thing at the time.

The numeric prefixes make the pipeline order visible in a directory listing, and make the
phase boundary visible too: everything up to `04-cko.json` is Phase A, everything after is
Phase B.

Paths are relative and unprefixed by contract id. `WorkspaceStore` is already scoped to one
contract — every query filters on `contract_id` unconditionally — so spec 05 §8's
`/work/<contract_id>/...` would be an interpolation that buys nothing and has to be built
and parsed at every call site.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from backend.schemas.cko import (
    ClauseCandidateSet,
    ContractKnowledgeObject,
    ContractMetadata,
    SemanticStructure,
)
from backend.schemas.intent import IntentObject, ResolutionPlan
from backend.schemas.plan import DraftPlan, TransformationPlan
from backend.schemas.recommendation import ClauseRecommendationSet
from backend.schemas.template import TemplateObject
from backend.schemas.validation import GateReport

__all__ = ["Artifact"]


class Artifact(Enum):
    """An artifact's canonical path and the model it holds.

    Binding the two together means a caller cannot read `04-cko.json` into the wrong type,
    and cannot invent a second path for an artifact that already has one.
    """

    # ---- Phase A: contract intelligence -------------------------------------------
    INTENT = ("work/00-intent.json", IntentObject)
    RESOLUTION = ("work/01-resolution-plan.json", ResolutionPlan)
    TEMPLATE = ("work/02-template.json", TemplateObject)
    #: The three understanding agents. Separate artifacts because they have separate
    #: consumers: contract review reads metadata alone, and recommendation reads only the
    #: clause candidates.
    UNDERSTANDING = ("work/03-understanding.json", SemanticStructure)
    METADATA = ("work/03-metadata.json", ContractMetadata)
    CLAUSE_CANDIDATES = ("work/03-clause-candidates.json", ClauseCandidateSet)
    #: The phase boundary. Everything above is an input to it; everything below reads it.
    CKO = ("work/04-cko.json", ContractKnowledgeObject)

    # ---- Phase B: drafting ---------------------------------------------------------
    DRAFT_PLAN = ("work/05-draft-plan.json", DraftPlan)
    #: Drafting refuses to start unless this exists. See `backend/invariants/phase_gate.py`.
    TRANSFORMATION_PLAN = ("work/06-transformation-plan.json", TransformationPlan)
    #: Retains rejected alternatives — "why this clause?" is answered by reading it.
    CLAUSE_RECOMMENDATIONS = ("work/07-clause-recommendations.json", ClauseRecommendationSet)
    #: The two validators. A blocker in either refuses finalization.
    VALIDATION_LEGAL = ("work/09-validation-legal.json", GateReport)
    VALIDATION_DOCUMENT = ("work/09-validation-document.json", GateReport)

    def __init__(self, path: str, model: type[BaseModel]) -> None:
        self._path = path
        self._model = model

    @property
    def path(self) -> str:
        return self._path

    @property
    def model(self) -> type[BaseModel]:
        return self._model


#: Artifacts written one-per-document rather than once per run. A directory, so it carries
#: a prefix rather than a path and is listed rather than read by name.
REFERENCE_PREFIX = "work/02-references/"
