"""Phase A, composed: request in, Contract Knowledge Object out.

The individual stages exist (intent, resolver, gather, understanding, aggregator); this is
the driver that runs them in order and folds their outputs into one CKO. It is the whole of
Phase A behind a single call, and it produces exactly one thing — the object Phase B
receives.

## The one piece of real integration

Two sources produce clause-level knowledge, and they arrive by different routes:

- **Providers** (`gather`) return `KnowledgeContribution`s directly — the clause library's
  candidates, the playbook's requirements, the template's formatting, the references' graphs.
- **The understanding engine** interprets an uploaded template into sections, metadata and
  clause candidates. Its output is not a contribution; it is three artifacts.

So when a template was resolved, this driver runs understanding over it and turns the result
into one more contribution, at the template's precedence, before aggregating. That is the
join the earlier milestones left implicit — M6 built the engine, M7 built the aggregator, and
this is where they meet.

## Suspension

`determine_intent` may not return — a low-confidence or unsupported request stops the run to
ask the user (M4). This driver therefore calls it first and lets it decide; everything after
runs only once intent is settled.
"""

from __future__ import annotations

from backend.artifacts import Artifact, ArtifactStore
from backend.core.logging import get_logger
from backend.core.run_context import RunContext
from backend.phase_a.build_cko import build_and_store_cko
from backend.phase_a.consent import consent_question, has_consent, needs_consent
from backend.phase_a.gather import gather
from backend.phase_a.intent import GATEWAY, determine_intent
from backend.phase_a.resolver import resolve
from backend.phase_a.understanding import understand
from backend.schemas.cko import ContractKnowledgeObject, Provenance
from backend.schemas.intent import IntentObject, ResolutionPlan
from backend.schemas.provider import KnowledgeContribution
from backend.schemas.template import TemplateObject
from backend.storage import get_storage

__all__ = ["run_phase_a"]

log = get_logger(__name__)


async def run_phase_a(request: str, ctx: RunContext) -> ContractKnowledgeObject:
    """Understand the request and everything the resolved providers know, as a CKO.

    May not return: `determine_intent` stops the run to ask the user when the request is
    not safe to act on.
    """
    intent = await determine_intent(request, ctx)  # may suspend
    plan = await resolve(intent, ctx)
    await _require_consent_if_unbacked(intent, plan, ctx)  # may suspend
    contributions = list(await gather(plan, intent, ctx))

    understanding = await _understand_template_if_present(plan, ctx)
    if understanding is not None:
        contributions.append(understanding)

    cko = await build_and_store_cko(tuple(contributions), intent, plan, ctx)
    log.info(
        "phase A complete: %d contribution(s), %d clause candidate(s), %d conflict(s)",
        len(contributions),
        len(cko.clause_candidates),
        len(cko.conflicts),
    )
    return cko


async def _require_consent_if_unbacked(
    intent: IntentObject, plan: ResolutionPlan, ctx: RunContext
) -> None:
    """Stop and ask before drafting a contract nothing but the model can vouch for.

    **May not return** — the gateway suspends the run, exactly as the intent stage does.
    Placed after resolution because availability is what decides it: whether approved clauses
    exist for this type is not known until the providers have been asked.
    """
    if not needs_consent(plan) or await has_consent(ctx):
        return

    log.info("no backed source for %s; asking to draft from model knowledge", intent.contract_type)
    # Last statement: under a suspending gateway the stack unwinds here.
    await GATEWAY.ask(ctx.session_factory, ctx.contract_id, [consent_question(intent)], "")


async def _understand_template_if_present(
    plan: ResolutionPlan, ctx: RunContext
) -> KnowledgeContribution | None:
    """Interpret the uploaded template, if there is one, into a contribution.

    Skipped entirely when no template participated — there is nothing to understand, and
    running the three agents over an absent document would be three model calls for nothing.
    """
    if "template" not in plan.providers:
        return None

    artifacts = ArtifactStore(ctx.session_factory, ctx.contract_id)
    template = await artifacts.load(Artifact.TEMPLATE)
    assert isinstance(template, TemplateObject)

    texts = _block_texts(template)
    result = await understand(template, texts, ctx)

    return KnowledgeContribution(
        provider="template",
        provenance=Provenance(provider="template", locator="understanding"),
        confidence=result.structure.confidence,
        sections=result.structure.sections,
        definitions=result.structure.definitions,
        metadata=result.metadata,
        clause_candidates=result.clauses.candidates,
    )


def _block_texts(template: TemplateObject) -> dict[str, str]:
    """The source document's block-id → text map, from the stored bytes.

    The bytes are in blob storage under the template's key. Recovering the text by parsing
    them keeps the CKO free of the raw document — the text is read here, used to feed the
    understanding agents, and not retained.
    """
    from backend.invariants.docx_parse import block_texts

    return block_texts(get_storage().get(template.storage_key))
