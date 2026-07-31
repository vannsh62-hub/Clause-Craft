"""Deciding the shape of the contract, before any of it is written.

Two agents, run in order:

1. **Draft planning** — what sections should exist, and in what order.
2. **Transformation planning** — what happens to each: KEEP, MODIFY, REMOVE, ADD.

The second is the pivot of the whole system. It is the difference between *converting* a
contract and *regenerating* one, and it produces `06-transformation-plan.json`, the artifact
that drafting is forbidden to proceed without.

## Context is deliberately starved

The transformation planner is given the CKO and nothing about how the contract will be
drafted. It does not see a draft, a drafting rationale, or the draft plan's prose — only
what the document *is* (its sections) and what it *must* contain (playbook requirements).
This is not an oversight. A planner shown the drafting agent's reasoning would argue itself
into agreement with it; the point of planning first is to decide independently, on the
evidence, what should change.

## Phase B, so no reaching back

Both agents read the CKO and only the CKO. They import nothing from Phase A — if a fact
they need is not in the CKO, the answer is to put it in the CKO, not to re-open the source.
`test_phase_isolation.py` enforces this.
"""

from __future__ import annotations

from backend.artifacts import Artifact, ArtifactStore
from backend.clauselib.loader import clauses_for
from backend.core.config import settings
from backend.core.prompts import load_prompt
from backend.core.run_context import RunContext
from backend.runtime.adapters.openai_agents import runtime
from backend.runtime.port import AgentRuntime
from backend.runtime.spec import AgentSpec
from backend.schemas.cko import ContractKnowledgeObject, SourceRef
from backend.schemas.plan import DraftPlan, SectionDecision, TransformationPlan

__all__ = [
    "RUNTIME",
    "build_draft_plan_spec",
    "build_transformation_spec",
    "plan_draft",
    "plan_transformation",
]

#: Replaced in tests. One seam for every spec-driven agent.
RUNTIME: AgentRuntime = runtime


def build_draft_plan_spec() -> AgentSpec[DraftPlan]:
    return AgentSpec(
        name="draft_plan_agent",
        prompt=load_prompt("draft_plan"),
        model=settings.draft_plan_model,
        output_model=DraftPlan,
        max_turns=2,
        temperature=0.0,
    )


def build_transformation_spec() -> AgentSpec[TransformationPlan]:
    """The pivot. Strongest model, temperature 0.

    The same document should not classify its sections two different ways on two runs; the
    decision is meant to be a reasoned reading of the evidence, not a roll.
    """
    return AgentSpec(
        name="transformation_agent",
        prompt=load_prompt("transformation"),
        model=settings.transformation_model,
        output_model=TransformationPlan,
        max_turns=2,
        temperature=0.0,
    )


def _cko_view(cko: ContractKnowledgeObject) -> str:
    """The CKO rendered for a planning agent.

    Its own JSON. The CKO is already a structured, human-readable record — the whole point
    of it — so there is nothing to gain from paraphrasing it into prose that could drift
    from the artifact a reviewer will read.
    """
    return cko.model_dump_json(indent=2)


async def plan_draft(cko: ContractKnowledgeObject, ctx: RunContext) -> DraftPlan:
    """Decide the sections, and persist `05-draft-plan.json`."""
    result = await RUNTIME.run(build_draft_plan_spec(), ctx, _cko_view(cko))
    assert result.output is not None
    plan = result.output

    await ArtifactStore(ctx.session_factory, ctx.contract_id).save(Artifact.DRAFT_PLAN, plan)
    return plan


def _transformation_for_generation(
    draft_plan: DraftPlan, contract_type: str
) -> TransformationPlan:
    """Mode 1's transformation plan, derived rather than reasoned.

    With no source document there is nothing a section could be classified *against*:
    KEEP, MODIFY and REMOVE all name a block that does not exist, so every planned section
    is an ADD and the classification carries no information. Asking a model to make that
    decision is not merely wasteful — it is where Mode 1 lost its contract. The
    transformation planner is given the CKO alone (see the module docstring), and in Mode 1
    the CKO has no sections, so the planner had nothing to work from and returned only the
    sections it could infer from playbook requirements. The draft plan's other sections —
    the parties, the services, the fees — were computed and then silently dropped.

    Deriving the plan here keeps the starved-context rule where it earns its keep, which is
    Mode 2, and makes Mode 1 deterministic.

    `section.order` is the draft-plan agent's own guess at position and is not trustworthy
    on its own — the agent has no visibility into the clause library's canonical `order`
    field, so a library-sourced section (e.g. "Non-Solicitation", library order 60, meant to
    sit near the end) can be assigned order 0 or 1 by the model and print as clause "1."
    instead. Library-sourced sections are matched back to the library by title and resorted
    by the library's own order; only sections the library doesn't know about (LLM-authored,
    template- or playbook-sourced) keep the draft plan's order.
    """
    by_title = {c.title.strip().lower(): c for c in clauses_for(contract_type)}

    def _position(section: object) -> tuple[int, int]:
        clause = by_title.get(section.name.strip().lower())  # type: ignore[attr-defined]
        if clause is not None:
            return (0, clause.order)
        return (1, section.order)  # type: ignore[attr-defined]

    ordered_sections = sorted(draft_plan.sections, key=_position)

    decisions: list[SectionDecision] = []
    for section in ordered_sections:
        clause = by_title.get(section.name.strip().lower())
        source_ref = SourceRef(provider="library", clause_id=clause.id) if clause else None
        decisions.append(
            SectionDecision(
                name=section.name,
                decision="add",
                reason=section.rationale,
                source_ref=source_ref,
            )
        )

    return TransformationPlan(add=tuple(decisions))


async def plan_transformation(cko: ContractKnowledgeObject, ctx: RunContext) -> TransformationPlan:
    """Classify every section, and persist `06-transformation-plan.json`.

    In template mode this is the pivot of the system and a model makes the call, given the
    CKO alone — deliberately not the draft plan's rationale. In generation mode there is
    nothing to classify, so the plan is derived from the draft plan instead.
    """
    artifacts = ArtifactStore(ctx.session_factory, ctx.contract_id)

    if cko.source_storage_key is None:
        draft_plan = await artifacts.load(Artifact.DRAFT_PLAN)
        assert isinstance(draft_plan, DraftPlan)
        plan = _transformation_for_generation(draft_plan, cko.intent.contract_type)
    else:
        result = await RUNTIME.run(build_transformation_spec(), ctx, _cko_view(cko))
        assert result.output is not None
        plan = result.output

    await artifacts.save(Artifact.TRANSFORMATION_PLAN, plan)
    return plan