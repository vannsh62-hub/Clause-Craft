"""Phase B receives a CKO and nothing else, and this is the test that keeps it true.

The two-phase architecture rests on one signature:

    async def run_drafting_engine(cko: ContractKnowledgeObject, ctx: RunContext) -> DraftOutcome

If a second knowledge parameter is ever added — `template=`, `clause_library=`,
`references=` — the boundary is gone, and gone in the most expensive way: silently, while
every diagram and docstring still describes it as intact. The failure has a specific author
and a specific moment. Someone is under deadline, the CKO is missing a fact a stage needs,
and adding `template=run.template` to the call is five minutes while adding the fact to the
CKO schema is an afternoon. This test makes the five-minute path fail loudly.

It is intentionally mechanical. A prose reminder in a docstring is what it is replacing, and
prose reminders do not fail CI.
"""

from __future__ import annotations

import inspect

from backend.core.run_context import RunContext
from backend.phase_b.engine import run_drafting_engine
from backend.schemas.cko import ContractKnowledgeObject


def test_the_signature_is_exactly_cko_and_ctx() -> None:
    params = inspect.signature(run_drafting_engine).parameters

    assert set(params) == {"cko", "ctx"}, (
        f"run_drafting_engine takes {sorted(params)}. Phase B receives the Contract "
        "Knowledge Object and nothing else. If a stage needs a fact, add it to the CKO "
        "(routine); do not add a knowledge parameter here (the boundary eroding)."
    )


def test_the_first_parameter_is_the_cko() -> None:
    # eval_str resolves the string annotations that `from __future__ import annotations`
    # leaves in engine.py, so this asserts the real type, not its name.
    params = list(inspect.signature(run_drafting_engine, eval_str=True).parameters.values())

    assert params[0].name == "cko"
    assert params[0].annotation is ContractKnowledgeObject


def test_the_second_parameter_is_the_run_context() -> None:
    params = list(inspect.signature(run_drafting_engine, eval_str=True).parameters.values())

    assert params[1].name == "ctx"
    assert params[1].annotation is RunContext


def test_no_parameter_is_a_knowledge_source() -> None:
    """Belt and braces: name the specific parameters that must never appear.

    The set-equality test above already catches these, but naming them turns a future
    failure from "the parameter set changed" into "you added `template`, which is what this
    test forbids".
    """
    forbidden = {"template", "clause_library", "clauses", "references", "reference", "playbook"}
    params = set(inspect.signature(run_drafting_engine).parameters)

    assert not (params & forbidden), (
        f"run_drafting_engine gained {sorted(params & forbidden)}. That knowledge already "
        "lives in the CKO; reaching around it defeats the phase boundary."
    )
