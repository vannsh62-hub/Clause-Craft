"""The composite judge.

Deterministic gates run first, and **short-circuit**: a draft with a missing clause, a
surviving placeholder, or reworded approved text never reaches the model at all. Zero tokens
are spent telling an LLM what a set difference already knows.

If the gates pass, the LLM judge scores the 30 points code cannot: consistency, formatting,
tone. It cannot overrule a blocker because it is never told one exists.

Two properties that are structural rather than prompted, each a real failure mode in judge
loops:

1. **The judge never sees its own prior score.** Sub-agents run with `session=None`, so there
   is no history to carry it. A judge shown its previous score anchors and inflates.
2. **The judge never sees the drafting agent's rationale.** It gets a draft path and reads the
   file itself. An explanation of why the draft is good is an argument, and the judge is not
   here to be argued with.
"""

from __future__ import annotations

import json

from agents import RunContextWrapper, function_tool

from backend.clauselib.loader import required_clause_ids
from backend.core.config import settings
from backend.core.prompts import load_prompt
from backend.core.run_context import RunContext
from backend.invariants.validate import score_draft, validate_draft
from backend.runtime.adapters.openai_agents import runtime
from backend.runtime.port import AgentRuntime
from backend.runtime.spec import AgentSpec
from backend.schemas.draft import ValidationReport
from backend.schemas.errors import ClauseError, format_tool_error
from backend.schemas.judge import JudgeVerdict
from backend.tools.validation_tool import load_rendered_clauses
from backend.workspace.ledger import latest_version
from backend.workspace.models import JudgeReport
from backend.workspace.store import WorkspaceStore

JUDGE_MAX_TURNS = 6
FINDINGS_PATH = "findings_v{n}.json"


#: The runtime this module executes on. Tests replace it with one carrying a fake model;
#: that is the single injection seam for every spec-driven agent, in place of a builder
#: per agent.
RUNTIME: AgentRuntime = runtime


def build_judge_spec() -> AgentSpec[JudgeVerdict]:
    """The judge, as data.

    Temperature 0: scoring the same draft twice should not produce two numbers. It reads
    and never writes — a judge that can re-render clauses can make its own complaints go
    away.
    """
    return AgentSpec(
        name="judge_agent",
        prompt=load_prompt("judge"),
        model=settings.judge_model,
        tools=("read_file", "ls_files"),
        output_model=JudgeVerdict,
        max_turns=JUDGE_MAX_TURNS,
        temperature=0.0,
    )


@function_tool(failure_error_function=format_tool_error)
async def run_judge_agent(wrapper: RunContextWrapper[RunContext]) -> str:
    """Score the most recent draft. Run this after every drafting attempt.

    The deterministic gates run first. If the draft is missing a required clause, still has a
    placeholder, or has reworded approved text, this returns immediately with those blockers
    and spends nothing — fix them and draft again. Otherwise the judge sub-agent scores the
    prose. The findings are written to `findings_v<attempt>.json`, which the next drafting
    attempt reads automatically.

    A draft carrying any blocker cannot score above 89, whatever the prose is like.
    """
    context = wrapper.context
    if context.contract_type is None:  # pragma: no cover - orchestrator decides this first
        raise ClauseError("contract type is not decided yet; call list_clause_library")

    async with context.session_factory() as session:
        version = await latest_version(session, context.contract_id)
    if version is None:
        raise ClauseError("there is no draft to judge; call run_drafting_agent first")

    expected = await load_rendered_clauses(context)
    required = required_clause_ids(context.contract_type, context.jurisdiction)
    report = validate_draft(version.markdown, expected, required)

    findings_path = FINDINGS_PATH.format(n=version.attempt)

    if report.blockers:
        score = score_draft(report, judge_points=0)
        await _record(context, findings_path, report, None, score, passed=False)
        lines = [
            f"BLOCKED at attempt {version.attempt}. {len(report.blockers)} blocker(s); "
            "the prose was not scored because the draft cannot be finalized as it stands:"
        ]
        lines += [f"  [{f.dimension}] {f.message} → {f.fix_hint}" for f in report.blockers]
        lines.append(f"Score {score}/100, capped at 89 until fixed. Draft again.")
        return "\n".join(lines)

    result = await RUNTIME.run(
        build_judge_spec(),
        context,
        f"Review the draft at `{version.path}` and return your verdict.",
    )
    # Validated by the port, not by this call site: `AgentResult.output` is a JudgeVerdict
    # or the run already raised. `output_model` is set, so it is never None here.
    assert result.output is not None
    verdict = result.output
    score = score_draft(report, judge_points=verdict.points)
    passed = score >= settings.judge_pass_score

    await _record(context, findings_path, report, verdict, score, passed=passed)

    headline = (
        f"attempt {version.attempt} scored {score}/100 "
        f"(gates {report.deterministic_points}/70, prose {verdict.points}/30) — "
        f"{'PASS' if passed else 'below the pass mark of ' + str(settings.judge_pass_score)}"
    )
    if not verdict.findings:
        return headline
    detail = "\n".join(f"  [{f.dimension}] {f.message} → {f.fix_hint}" for f in verdict.findings)
    return f"{headline}\nFindings written to {findings_path}:\n{detail}"


async def _record(
    context: RunContext,
    findings_path: str,
    report: ValidationReport,
    verdict: JudgeVerdict | None,
    score: int,
    *,
    passed: bool,
) -> None:
    """Write the findings file and score the attempt.

    `verdict` is `None` when the deterministic gates blocked the draft and the model was never
    called — the findings file then holds blockers and no prose section.
    """
    blockers = [f.model_dump() for f in report.blockers]
    payload: dict[str, object] = {
        "score": score,
        "passed": passed,
        "deterministic_points": report.deterministic_points,
        "blockers": blockers,
        "prose": verdict.model_dump() if verdict else None,
    }

    async with context.session_factory() as session:
        await WorkspaceStore(session).write(
            context.contract_id, findings_path, json.dumps(payload, indent=2, sort_keys=True)
        )
        version = await latest_version(session, context.contract_id)
        if version is not None:
            version.score = score
            version.passed = passed
            version.clause_ids = list(report.present_clause_ids)
            session.add(
                JudgeReport(
                    contract_version_id=version.id,
                    judge_points=verdict.points if verdict else 0,
                    deterministic_points=report.deterministic_points,
                    score=score,
                    passed=passed,
                    findings=blockers,
                )
            )
        await session.commit()
