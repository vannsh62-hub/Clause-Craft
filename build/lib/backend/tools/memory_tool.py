"""Memory, as tools.

The orchestrator decides; memory only offers. `recall_memory` never returns "here is the
effective date" — it returns facts with their provenance, and says plainly which of them are
good enough to use and which are questions wearing a good prior.

That distinction is the feature. A system that silently remembers your jurisdiction and writes
it into a contract is *worse* than one that asks, because you no longer know which values you
chose and which the machine did.
"""

from __future__ import annotations

from agents import RunContextWrapper, function_tool

from backend.core.principal import current_principal
from backend.core.run_context import RunContext
from backend.memory.stability import MEMORABLE
from backend.memory.store import Conflict, MemoryRefused, MemoryStore
from backend.schemas.errors import ContractToolError, format_tool_error
from backend.tools.guard import loop_guard

__all__ = ["forget_memory", "recall_memory", "remember_fact"]


def _describe(hit: object) -> str:
    from backend.memory.store import MemoryHit

    assert isinstance(hit, MemoryHit)
    when = hit.confirmed_at.strftime("%d %b %Y")

    if hit.usable_without_asking:
        return f"  {hit.key} = {hit.value!r}  (you confirmed this on {when} — usable)"
    if hit.stale:
        return (
            f"  {hit.key} = {hit.value!r}  (confirmed {when}, now STALE — "
            f"confirm it with the user before using it)"
        )
    return (
        f"  {hit.key} = {hit.value!r}  (inferred, confidence {hit.confidence:.1f} — "
        f"NOT confirmed. Ask the user; do not fill this in.)"
    )


@function_tool(failure_error_function=format_tool_error)
@loop_guard
async def recall_memory(wrapper: RunContextWrapper[RunContext], keys: list[str]) -> str:
    """What the system already knows about this user. Call this **before** `ask_user`.

    Memory holds who the user is and how they like their contracts — never the particulars of a
    deal. A counterparty, an effective date, a fee are asked for every time.

    A returned fact is only an answer if it says `usable`. A `STALE` or `NOT confirmed` fact is a
    **question with a good prior**: put it to the user as a suggested default, do not fill it in.

    Whenever you use a recalled value, tell the user you did, and when they confirmed it.

    Args:
        keys: what to look up. Known keys: my_company_name, my_company_address, my_signatory,
            preferred_governing_law_country, preferred_jurisdiction_city,
            preferred_duration_years, preferred_payment_days, preferred_notice_days,
            preferred_currency.
    """
    context = wrapper.context
    async with context.session_factory() as session:
        hits = await MemoryStore(session, current_principal()).recall(keys)

    if not hits:
        return (
            "Nothing remembered for those keys. Ask the user. "
            f"(Memorable keys: {', '.join(sorted(MEMORABLE))})"
        )

    usable = [h for h in hits if h.usable_without_asking]
    lines = [f"{len(hits)} fact(s) remembered; {len(usable)} usable without asking:"]
    lines += [_describe(h) for h in sorted(hits, key=lambda h: h.key)]
    return "\n".join(lines)


@function_tool(failure_error_function=format_tool_error)
@loop_guard
async def remember_fact(
    wrapper: RunContextWrapper[RunContext], key: str, value: str, user_confirmed: bool
) -> str:
    """Remember something the user told you, so you need not ask again next time.

    Only call this for a value **the user actually gave you in this conversation**. Never store
    something you inferred from a document, and never store a deal particular — a counterparty,
    an effective date, a fee. Those are asked every time, and this tool will refuse them.

    If the user tells you something that contradicts what is remembered, this returns a CONFLICT.
    Do not resolve it yourself: show the user both values and their dates, ask which is right,
    and then call this again with `user_confirmed=true` to overwrite.

    Args:
        key: one of the memorable keys. `recall_memory` lists them.
        value: what the user said.
        user_confirmed: true only if the user stated this themselves, or explicitly agreed to it.
    """
    if not user_confirmed:
        raise ContractToolError(
            "Only a value the user actually gave you may be remembered. If you inferred it, "
            "ask them to confirm it first."
        )

    context = wrapper.context
    try:
        async with context.session_factory() as session:
            store = MemoryStore(session, current_principal())
            outcome = await store.remember(key, value)

            if isinstance(outcome, Conflict):
                when = outcome.existing_confirmed_at.strftime("%d %b %Y")
                return (
                    f"CONFLICT on {key}. Remembered: {outcome.existing!r} (confirmed {when}). "
                    f"You are proposing: {outcome.proposed!r}. Nothing was written.\n"
                    "Ask the user which is right — this may be a change of preference, or it may "
                    "be a one-off for this deal. If they confirm the new value, call "
                    "resolve_memory_conflict."
                )

            await session.commit()
    except MemoryRefused as exc:
        raise ContractToolError(str(exc)) from exc

    return f"Remembered {key} = {value!r}. You will not need to ask for this next time."


@function_tool(failure_error_function=format_tool_error)
@loop_guard
async def resolve_memory_conflict(
    wrapper: RunContextWrapper[RunContext], key: str, value: str
) -> str:
    """Overwrite a remembered fact, after the user has told you which value is right.

    Only call this once the user has seen both values and chosen. The old value is kept in the
    history — nothing is lost — but it stops being recalled.

    Args:
        key: the key that conflicted.
        value: the value the user confirmed.
    """
    context = wrapper.context
    try:
        async with context.session_factory() as session:
            store = MemoryStore(session, current_principal())
            result = await store.supersede(key, value)
            await session.commit()
    except MemoryRefused as exc:
        raise ContractToolError(str(exc)) from exc

    was = f" (was {result.superseded!r})" if result.superseded else ""
    return f"Updated {key} = {value!r}{was}."


@function_tool(failure_error_function=format_tool_error)
@loop_guard
async def forget_memory(wrapper: RunContextWrapper[RunContext], key: str) -> str:
    """Forget a remembered fact, because the user asked you to.

    Args:
        key: the key to forget.
    """
    context = wrapper.context
    async with context.session_factory() as session:
        forgotten = await MemoryStore(session, current_principal()).forget(key)
        await session.commit()

    if not forgotten:
        return f"Nothing was remembered for {key}."
    return f"Forgotten: {key}. It will not be recalled again."
