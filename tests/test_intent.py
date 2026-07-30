"""Intent, and the three ways it refuses to proceed.

Spec 01's guardrail carried into Phase A: the service refuses contract types it has no
competence in, and asks rather than guessing when it is unsure. The mechanism changed —
an allow-list plus a confidence score, rather than the presence of a clause folder, because
Mode 1 has no clause folder — but the guarantee is the same. A tool that confidently drafts
anything is a liability.

Every refusal is checked in code. A prompt can ask a model to be uncertain; it cannot make
the pipeline act on that uncertainty, and a model asked to police its own confidence
threshold will quietly drift over it.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.artifacts import Artifact, ArtifactStore
from backend.core.config import settings
from backend.core.run_context import RunContext
from backend.phase_a import intent as intent_mod
from backend.runtime.adapters.openai_agents import OpenAIAgentsRuntime
from backend.schemas.errors import SuspendRun
from backend.schemas.intent import IntentObject
from backend.workspace.models import Contract, PendingQuestion
from tests.fakes import FakeModel, Turn, text_message

CLEAN = {
    "contract_type": "nda",
    "parties": [{"name": "ProcBay", "role": "Disclosing Party"}],
    "country": "IN",
    "jurisdiction": "IN",
    "language": "en",
    "purpose": "protect disclosures during evaluation",
    "mode": "ai_drafting",
    "confidence": 0.95,
    "needs_clarification": [],
}


def _model(**overrides: object) -> FakeModel:
    payload = {**CLEAN, **overrides}
    return FakeModel([Turn(output=[text_message(json.dumps(payload))])])


@pytest_asyncio.fixture
async def ctx() -> AsyncIterator[RunContext]:
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    cid = uuid.uuid4()
    async with factory() as s:
        s.add(Contract(id=cid, contract_type="nda", request="Draft an NDA"))
        await s.commit()
    try:
        yield RunContext(contract_id=cid, session_factory=factory, contract_type="nda")
    finally:
        async with factory() as s:
            await s.execute(delete(Contract).where(Contract.id == cid))
            await s.commit()
        await engine.dispose()


def _use(monkeypatch: pytest.MonkeyPatch, model: FakeModel) -> None:
    monkeypatch.setattr(intent_mod, "RUNTIME", OpenAIAgentsRuntime(model))


# ----------------------------------------------------------------------- the happy path


async def test_a_clear_request_produces_intent_and_proceeds(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, _model())

    intent = await intent_mod.determine_intent("Draft an NDA between ProcBay and Acme.", ctx)

    assert intent.contract_type == "nda"
    assert intent.parties[0].name == "ProcBay"
    assert intent.confidence == 0.95


async def test_the_intent_artifact_is_written(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, _model())

    await intent_mod.determine_intent("Draft an NDA.", ctx)

    stored = await ArtifactStore(ctx.session_factory, ctx.contract_id).load(Artifact.INTENT)
    assert isinstance(stored, IntentObject)
    assert stored.contract_type == "nda"


# --------------------------------------------------------------------- the three refusals


async def test_low_confidence_asks_instead_of_guessing(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The threshold is enforced here, not by the model."""
    _use(monkeypatch, _model(confidence=0.3))

    with pytest.raises(SuspendRun) as caught:
        await intent_mod.determine_intent("Draft me something for the Acme deal.", ctx)

    asked = " ".join(q["question"] for q in caught.value.questions)
    assert "30%" in asked and "confident" in asked


async def test_a_second_ask_on_the_same_contract_does_not_collide(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asking twice is normal: the user answers, intent is still short, so it asks again.

    `pending_questions` is unique on `(contract_id, call_id)`, so a fixed call id made the
    second ask raise `IntegrityError` from inside the gateway — the run died mid-resume with
    no question and no error the user could see. Each ask gets its own id.
    """
    _use(monkeypatch, _model(confidence=0.3))
    with pytest.raises(SuspendRun):
        await intent_mod.determine_intent("Draft me something.", ctx)

    _use(monkeypatch, _model(confidence=0.3))
    with pytest.raises(SuspendRun):  # must be the suspension, never an IntegrityError
        await intent_mod.determine_intent("Draft me something, for Acme.", ctx)

    async with ctx.session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(PendingQuestion).where(
                        PendingQuestion.contract_id == ctx.contract_id,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 2, "both asks were recorded"
    assert len({r.call_id for r in rows}) == 2, "each ask has its own call id"


async def test_an_unfamiliar_contract_type_is_not_refused(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user may ask for any document; the engine drafts it or says why it cannot.

    Intent used to refuse anything outside a five-name allow-list — "I do not draft
    'employment' agreements" — which turned away requests the engine drafts perfectly well
    and said nothing about the ones it writes from model knowledge. The competence question
    is now asked where the answer is actually known: the consent gate, after resolution, once
    it is clear whether approved clauses exist. See `test_consent_gate.py`.
    """
    _use(monkeypatch, _model(contract_type="employment", confidence=0.99))

    intent = await intent_mod.determine_intent("Draft an employment agreement.", ctx)

    assert intent.contract_type == "employment", "the type is taken at face value"


async def test_asking_stops_after_the_allowance_and_the_run_proceeds(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model that never gains confidence must not be able to ask forever.

    The live failure: "I am only 30% confident this is a 'lease'" asked five times over, the
    user answering each time. Instructing the agent to stop asking cannot fix it, because the
    confidence check is enforced *in code* — so the stop has to be too.
    """
    from sqlalchemy import func as sa_func

    from backend.workspace.models import PendingQuestion

    async with ctx.session_factory() as session:
        for n in range(settings.max_ask_rounds):
            session.add(
                PendingQuestion(
                    contract_id=ctx.contract_id,
                    call_id=f"round-{n}",
                    questions=[{"name": "clarification_1", "question": "Which?", "type": "text"}],
                    answers={"clarification_1": "a home rent agreement"},
                    answered_at=sa_func.now(),
                )
            )
        await session.commit()

    _use(monkeypatch, _model(contract_type="lease", confidence=0.3))

    intent = await intent_mod.determine_intent("create home rent agreement", ctx)

    assert intent.contract_type == "lease", "it proceeds with what it has rather than asking"


async def test_below_the_allowance_it_still_asks(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap must not swallow the first, legitimate clarification."""
    _use(monkeypatch, _model(confidence=0.3))

    with pytest.raises(SuspendRun):
        await intent_mod.determine_intent("Something for Acme.", ctx)


async def test_the_models_own_questions_are_taken_at_their_word(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, _model(needs_clarification=["Is this mutual or one-way?"]))

    with pytest.raises(SuspendRun) as caught:
        await intent_mod.determine_intent("Draft an NDA.", ctx)

    assert caught.value.questions[0]["question"] == "Is this mutual or one-way?"


async def test_every_reason_is_asked_at_once(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One round trip, not three.

    A user asked three questions in sequence, one per suspend/resume cycle, will
    reasonably conclude the tool is wasting their time.
    """
    _use(
        monkeypatch,
        _model(
            contract_type="maritime_charter",
            confidence=0.2,
            needs_clarification=["Who are the parties?", "What is the term?"],
        ),
    )

    with pytest.raises(SuspendRun) as caught:
        await intent_mod.determine_intent("Something for Acme.", ctx)

    assert len(caught.value.questions) == 3


# ------------------------------------------------------------------------- persistence


async def test_the_artifact_survives_a_suspension(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Written before the gateway is called.

    A resumed run reads what was understood rather than re-deriving it, and a human
    debugging the question can see what prompted it.
    """
    _use(monkeypatch, _model(confidence=0.2))

    with pytest.raises(SuspendRun):
        await intent_mod.determine_intent("Draft something.", ctx)

    stored = await ArtifactStore(ctx.session_factory, ctx.contract_id).load(Artifact.INTENT)
    assert isinstance(stored, IntentObject)
    assert stored.confidence == 0.2


async def test_the_questions_are_committed_so_the_run_can_be_resumed(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, _model(confidence=0.2))

    with pytest.raises(SuspendRun):
        await intent_mod.determine_intent("Draft something.", ctx, call_id="call-9")

    async with ctx.session_factory() as s:
        rows = (
            (
                await s.execute(
                    select(PendingQuestion).where(PendingQuestion.contract_id == ctx.contract_id)
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].call_id == "call-9"


# ------------------------------------------------------------------- the checks in code


@pytest.mark.parametrize(
    ("confidence", "contract_type", "expected"),
    [
        (0.95, "nda", 0),
        (0.5, "nda", 1),
        # The type no longer decides anything here: an unfamiliar one is drafted or
        # disclosed by the consent gate, never refused for being unfamiliar.
        (0.95, "charterparty", 0),
        (0.5, "charterparty", 1),
    ],
)
def test_unmet_conditions_is_a_pure_function(
    confidence: float, contract_type: str, expected: int
) -> None:
    """Cheap to test exhaustively because no model is involved in the decision."""
    intent = IntentObject(contract_type=contract_type, confidence=confidence)
    assert len(intent_mod.unmet_conditions(intent)) == expected


def test_the_threshold_comes_from_config_not_from_a_literal() -> None:
    at_threshold = IntentObject(
        contract_type="nda", confidence=settings.intent_confidence_threshold
    )
    below = IntentObject(
        contract_type="nda", confidence=settings.intent_confidence_threshold - 0.01
    )

    assert intent_mod.unmet_conditions(at_threshold) == ()
    assert intent_mod.unmet_conditions(below)


def test_the_intent_agent_has_no_tools() -> None:
    """A stage that could look things up would blur what the user said with what the
    system found out."""
    spec = intent_mod.build_intent_spec()
    assert spec.tools == ()
    assert spec.output_model is IntentObject
    assert spec.temperature == 0.0
