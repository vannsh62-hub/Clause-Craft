"""Understanding the request, and refusing to guess.

The first stage of Phase A and the only one that talks to the user. It produces an
`IntentObject` and, when it cannot produce a trustworthy one, stops the run and asks.

Three refusal conditions, all enforced in code rather than in the prompt. A prompt can ask
a model to be uncertain; it cannot make the pipeline act on that uncertainty:

1. **The model said it needs clarification.** Taken at its word.
2. **Confidence is below the threshold.** Checked here, not by the model. A model that is
   asked to police its own confidence threshold will quietly drift over it.
3. **The contract type is outside the allow-list.** Spec 01's guardrail, carried forward:
   this service refuses contract types it has no competence in. Under Mode 1 the check is
   an allow-list plus a confidence score rather than the presence of a clause folder,
   because Mode 1 has no clause folder — but the guarantee is the same one. A tool that
   confidently drafts anything is a liability.

Asking happens **here, at the start**, and that placement is the point. Clarification is
cheap before knowledge is gathered and expensive after drafting has begun: a question
answered at intent time invalidates one artifact; the same question at drafting time
invalidates six.
"""

from __future__ import annotations

import uuid

from backend.artifacts import Artifact, ArtifactStore
from backend.core.config import settings
from backend.core.logging import get_logger
from backend.core.prompts import load_prompt
from backend.core.run_context import RunContext
from backend.runtime.adapters.openai_agents import runtime
from backend.runtime.human import SuspendingGateway
from backend.runtime.port import AgentRuntime
from backend.runtime.spec import AgentSpec
from backend.schemas.intent import IntentObject
from backend.schemas.question import Question

__all__ = ["GATEWAY", "RUNTIME", "build_intent_spec", "determine_intent", "unmet_conditions"]

log = get_logger(__name__)

#: Replaced in tests. See `judge_agent.RUNTIME` — one seam for every spec-driven agent.
RUNTIME: AgentRuntime = runtime

#: How this stage asks. `SuspendingGateway` never returns; it ends the run slice.
GATEWAY = SuspendingGateway()


def build_intent_spec() -> AgentSpec[IntentObject]:
    """The Intent Agent, as data.

    No tools. It reads the request and reports what is in it — a stage that could go and
    look things up would be answering a different question, and would blur the line between
    what the user said and what the system found out.

    Temperature 0: the same request should not yield two different contract types.
    """
    return AgentSpec(
        name="intent_agent",
        prompt=load_prompt("intent"),
        model=settings.intent_model,
        output_model=IntentObject,
        max_turns=2,
        temperature=0.0,
    )


def unmet_conditions(intent: IntentObject, *, has_template: bool = False) -> tuple[str, ...]:
    """Reasons this intent is not safe to act on. Empty means proceed.

    Returns all of them rather than the first. A user asked three questions in sequence,
    one per round trip, will reasonably conclude the tool is wasting their time.

    **The contract type is no longer one of these reasons.** It used to be: a type outside
    `settings.supported_contract_types` was refused outright with "I do not draft
    'employment' agreements". That guardrail predated the clause library being only one of
    several knowledge sources, and it answered the wrong question — it refused *unfamiliar*
    types rather than disclosing *unbacked* ones, so it turned away requests the engine can
    perfectly well draft while saying nothing about the ones it drafts from model knowledge.

    That job now belongs to the consent gate (`phase_a/consent.py`), which runs after
    resolution — when whether approved clauses exist is actually known — and asks rather than
    refuses. `has_template` is kept for callers and is no longer load-bearing here.
    """
    reasons: list[str] = []

    if intent.needs_clarification:
        reasons.extend(intent.needs_clarification)

    if intent.confidence < settings.intent_confidence_threshold:
        reasons.append(
            f"I am only {intent.confidence:.0%} confident this is a "
            f"{intent.contract_type!r}. What kind of agreement do you need?"
        )

    return tuple(reasons)


def _questions(reasons: tuple[str, ...]) -> list[Question]:
    return [
        Question(name=f"clarification_{n}", question=reason, type="text")
        for n, reason in enumerate(reasons, start=1)
    ]


async def _asked_enough(ctx: RunContext) -> bool:
    """Whether this contract has used up its allowance of questions.

    Counted from the ledger rather than held in memory, because asking *is* the thing that
    ends the run slice — nothing in this process survives to remember it.
    """
    from backend.workspace.ledger import count_answered_ask_rounds

    async with ctx.session_factory() as session:
        rounds = await count_answered_ask_rounds(session, ctx.contract_id)
    return rounds >= settings.max_ask_rounds


async def _template_uploaded(ctx: RunContext) -> bool:
    """Whether a template document was stored for this run — a cheap workspace lookup.

    Imported lazily: intent is a Phase A stage and importing a knowledge provider at module
    load would couple the two more tightly than a single pointer path warrants.
    """
    from backend.knowledge.providers.template import POINTER_PATH
    from backend.workspace.store import WorkspaceStore

    async with ctx.session_factory() as session:
        return await WorkspaceStore(session).exists(ctx.contract_id, POINTER_PATH)


async def determine_intent(request: str, ctx: RunContext, *, call_id: str = "") -> IntentObject:
    """Determine intent, persist it, and stop to ask if it is not trustworthy.

    **May not return.** When clarification is required this calls the human gateway, which
    under the shipped implementation raises and ends the run slice. The artifact is written
    *before* the gateway is called, so a run that suspends still leaves behind what it
    understood — a resumed run reads it rather than re-deriving it, and a human debugging
    the question can see what prompted it.

    `call_id` identifies this particular ask. It defaults to a fresh id because
    `pending_questions` is unique on `(contract_id, call_id)`: a run that asks, is answered,
    and needs to ask again would otherwise collide with its own first question and die with
    an integrity error instead of asking. Callers pass an explicit id only when they need the
    ask to be idempotent.
    """
    call_id = call_id or uuid.uuid4().hex
    result = await RUNTIME.run(build_intent_spec(), ctx, request)
    assert result.output is not None  # output_model is set, so the port validated it
    intent = result.output

    # A template turns this into Mode 2: record the mode so downstream stages and the CKO
    # gate agree, and relax the type allow-list, since the template is the knowledge source.
    has_template = await _template_uploaded(ctx)
    if has_template and intent.mode != "template":
        intent = intent.model_copy(update={"mode": "template"})

    await ArtifactStore(ctx.session_factory, ctx.contract_id).save(Artifact.INTENT, intent)

    reasons = unmet_conditions(intent, has_template=has_template)
    if reasons and await _asked_enough(ctx):
        # The user has answered as many times as we are willing to ask. Proceed with what we
        # have rather than asking a fourth time: the checks below are enforced in code, so an
        # agent that keeps reporting low confidence would otherwise suspend the run forever
        # while the user keeps answering. The draft still has to pass the validation gates.
        log.warning(
            "intent=%s confidence=%.2f: proceeding after %d ask rounds without asking again",
            intent.contract_type,
            intent.confidence,
            settings.max_ask_rounds,
        )
        return intent

    if reasons:
        log.info(
            "intent=%s confidence=%.2f asking=%d",
            intent.contract_type,
            intent.confidence,
            len(reasons),
        )
        # Last statement: under a suspending gateway the stack unwinds here.
        await GATEWAY.ask(ctx.session_factory, ctx.contract_id, _questions(reasons), call_id)

    return intent
