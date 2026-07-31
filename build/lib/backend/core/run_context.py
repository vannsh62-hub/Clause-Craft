"""The object threaded through every tool call, and never through the model.

Passed as `Runner.run(context=...)`; tools receive it as `RunContextWrapper[RunContext]`
and read `wrapper.context`. The SDK never serialises it into the model's context window, so
the agent cannot see the attempt counter, argue with the cost ceiling, or read the database
handle.

`draft_attempts` and `cost_cents` are a **cache, not the ledger**. A fresh `RunContext` is
built for every `Runner.run`, and `ask_user` ends one run and starts another — so these must
be rehydrated from Postgres at the start of each slice, or a suspend/resume cycle silently
hands the agent a brand-new attempt budget. See M7.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass
class RunContext:
    contract_id: uuid.UUID
    session_factory: async_sessionmaker[AsyncSession]

    contract_type: str | None = None
    jurisdiction: str = "IN"

    #: Rehydrated from `count(contract_versions)` at slice start. Never trusted from memory.
    draft_attempts: int = 0

    #: The budget is denominated in **tokens, not currency**. Token counts come back exact
    #: from the API; prices are deployment-specific and change under us. A ceiling on a number
    #: we measure beats a ceiling on a number we would have to hardcode. Sub-agent usage is
    #: accumulated here too — otherwise delegation is a way to spend an unbounded budget.
    input_tokens: int = 0
    output_tokens: int = 0
    model_requests: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    #: fingerprint -> times called, within this slice. A resume is genuine new intent, so
    #: this window may reset; it exists to stop a wedged agent, not to budget across runs.
    tool_call_log: dict[str, int] = field(default_factory=dict)
