"""Assembling a drafted contract into a document.

The drafting agent writes clause text and recitals. Everything else a contract needs — its
title, the party block, the execution page — is *fact*, and facts are already in the CKO. So
they are derived here rather than asked of a model:

    facts  → derived (title, preamble, signatories)
    prose  → written (recitals, clause text)

That split is why the envelope is reliable. A model asked to remember a signature block will
sometimes forget it; a model asked to invent a party name must not be given the chance. The
CKO already holds the parties, the effective date and the governing law, so the deterministic
half of the document costs nothing and cannot drift.

Generation mode only. In template mode the uploaded document supplies its own envelope, and
inventing a second one over the top of it would be exactly the regeneration Mode 2 exists to
avoid.
"""

from __future__ import annotations

from backend.clauselib.loader import ClauseLibraryError, get_clause
from backend.schemas.cko import ContractKnowledgeObject
from backend.schemas.document import ClauseNode, ContractDocument, Signatory
from backend.schemas.plan import SectionDecision, TransformationPlan

__all__ = ["build_contract_document", "title_for"]

#: Display names for the contract types the engine drafts. A type with no entry falls back to
#: a title-cased form of its own name, so an unlisted type still gets a sensible title rather
#: than a bare slug.
_TITLES = {
    "nda": "Non-Disclosure Agreement",
    "service": "Services Agreement",
    "services": "Services Agreement",
    "sla": "Service Level Agreement",
    "msa": "Master Services Agreement",
    "dpa": "Data Processing Agreement",
}


#: A title is already a document name if it ends in one of these; otherwise it is a bare
#: subject ("Employment") and needs the noun.
_DOCUMENT_NOUNS = ("agreement", "contract", "deed", "licence", "license", "lease", "policy")


def title_for(cko: ContractKnowledgeObject) -> str:
    """The document's title. Prefers a name the metadata already carries.

    Any contract type is draftable, so most titles come from the fallback rather than the
    table — and a bare type name makes a poor title. "Employment" is a subject; "Employment
    Agreement" is a document.
    """
    named = (cko.metadata.contract_name or "").strip()
    if named:
        return named

    contract_type = (cko.intent.contract_type or "agreement").strip()
    known = _TITLES.get(contract_type.lower())
    if known:
        return known

    title = contract_type.replace("_", " ").title()
    if not title.lower().endswith(_DOCUMENT_NOUNS):
        title = f"{title} Agreement"
    return title


def _party_phrase(cko: ContractKnowledgeObject) -> str:
    """The parties, as they read in a preamble: `A (the "Role")` joined by `and`."""
    parts: list[str] = []
    for party in cko.intent.parties:
        if party.role:
            parts.append(f'{party.name} (the "{party.role}")')
        else:
            parts.append(party.name)
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return (
        " and ".join([", ".join(parts[:-1]), parts[-1]]) if len(parts) > 2 else " and ".join(parts)
    )


def _preamble(cko: ContractKnowledgeObject, title: str) -> str:
    """ "This Services Agreement is made on <date> between A and B."

    The date is omitted rather than invented when the CKO does not carry one — an undated
    preamble is normal in a draft, and a fabricated commencement date is not.
    """
    parties = _party_phrase(cko)
    if not parties:
        return ""
    when = f" on {cko.metadata.effective_date.isoformat()}" if cko.metadata.effective_date else ""
    return f"This {title} is made{when} between {parties}."


def _signatories(cko: ContractKnowledgeObject) -> tuple[Signatory, ...]:
    return tuple(Signatory(name=p.name, role=p.role) for p in cko.intent.parties)


def _clause_order(decision: SectionDecision, fallback: int) -> int:
    """The decision's intended position in the document, not its decision category.

    `[*plan.keep, *plan.modify, *plan.add]` groups sections by what happened to them
    (kept, rewritten, inserted), which is not the same as where they belong on the page —
    a KEEP clause meant to appear last would otherwise print first just because KEEP is
    listed first. The clause library's `order` field is the real source of truth for
    position, so it is looked up via the decision's `clause_id` whenever one is present.
    Sections with no library backing (e.g. a bespoke ADD) fall back to their original
    position in the concatenated list, so they don't jump around unpredictably.
    """
    clause_id = decision.source_ref.clause_id if decision.source_ref else None
    if clause_id:
        try:
            return get_clause(clause_id).order
        except ClauseLibraryError:
            pass
    return fallback


def _clauses(plan: TransformationPlan, new_text: dict[str, str]) -> tuple[ClauseNode, ...]:
    """The operative clauses, in document order, skipping anything marked for removal.

    Flat rather than nested: the draft plan has no notion of sub-clauses today, so inventing
    a hierarchy here would be a guess about structure the planner never expressed. The
    renderer supports depth whenever the plan grows it.
    """
    combined: list[SectionDecision] = [*plan.keep, *plan.modify, *plan.add]
    ordered = sorted(
        enumerate(combined),
        key=lambda pair: (_clause_order(pair[1], pair[0]), pair[0]),
    )
    nodes: list[ClauseNode] = []
    for _, decision in ordered:
        ref = (decision.source_ref.block_id if decision.source_ref else None) or decision.name
        nodes.append(ClauseNode(heading=decision.name, text=new_text.get(ref, "")))
    return tuple(nodes)


def build_contract_document(
    cko: ContractKnowledgeObject,
    plan: TransformationPlan,
    new_text: dict[str, str],
    recitals: tuple[str, ...] = (),
) -> ContractDocument:
    """Assemble everything into the document that will be rendered and validated."""
    title = title_for(cko)
    return ContractDocument(
        title=title,
        preamble=_preamble(cko, title),
        recitals=recitals,
        clauses=_clauses(plan, new_text),
        execution_note=(
            "IN WITNESS WHEREOF, the parties have executed this Agreement as of the date "
            "first written above."
        ),
        signatories=_signatories(cko),
    )