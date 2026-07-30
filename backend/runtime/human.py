"""Asking the user something, without assuming how the runtime pauses.

The two runtimes pause in fundamentally different ways, and this is the one place where
the abstraction cannot hide the difference — so it names it instead.

    openai-agents   there is no pause. `ask_user` persists the question, commits, and
                    raises a control signal. The run slice ends. A later slice resumes
                    with the answers as fresh input. `ask` never returns.
    LangGraph       `interrupt()` checkpoints the graph and, on resume, returns the value
                    at the call site. `ask` returns.

`HumanGateway.ask` is therefore documented as **MAY NOT RETURN**, and callers must obey
one rule:

    Commit everything you need, then ask, and let `ask` be the last thing you do.

That is already how `backend/tools/user_tool.py` is written. The gateway does not invent
the discipline; it names it and makes it testable. `tests/test_human_gateway_contract.py`
runs the same calling code against a gateway that raises and a gateway that returns, so a
caller that quietly relies on control coming back fails now rather than during a future
migration — where it would present as work silently going missing after a resume.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.schemas.errors import SuspendRun
from backend.schemas.question import Question
from backend.workspace.models import PendingQuestion

__all__ = ["HumanGateway", "SuspendingGateway"]


class HumanGateway(Protocol):
    """Obtain answers from the user.

    **MAY NOT RETURN.** A suspending implementation persists the questions and raises a
    control signal that ends the current run slice; the answers arrive as input to a
    later slice. Treat every call as the last statement in its scope, and commit any
    state you care about *before* calling.
    """

    async def ask(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        contract_id: uuid.UUID,
        questions: list[Question],
        call_id: str,
    ) -> dict[str, str]: ...


class SuspendingGateway:
    """Persist the questions and end the slice. Never returns.

    The commit before raising is load-bearing: the control signal unwinds the stack, and
    an uncommitted `PendingQuestion` would be rolled back with it — leaving a contract
    marked `awaiting_input` with nothing to answer.
    """

    async def ask(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        contract_id: uuid.UUID,
        questions: list[Question],
        call_id: str,
    ) -> dict[str, str]:
        payload = [q.model_dump() for q in questions]
        async with session_factory() as session:
            session.add(
                PendingQuestion(contract_id=contract_id, call_id=call_id, questions=payload)
            )
            await session.commit()
        raise SuspendRun(call_id, payload)
