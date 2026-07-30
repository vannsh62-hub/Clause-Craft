# Roadmap — AI Contract Drafting Platform

Version: 2.0 · **Phase 1 complete**
Companion documents: [PRD.md](./PRD.md) · [TDD.md](./TDD.md)

> **Version 1.0 of this document planned a system that was never built.** It milestoned a
> Planner agent, a Context Builder, and a `workflows/contract_workflow.py` orchestrator — a
> deterministic pipeline, not a deep agent. It also deferred the Judge to Phase 3, which would
> have made adding it a core-orchestration change and broken the extensibility claim it was
> written to protect. This document records what shipped and what remains.

---

## Phase 1 — shipped

Twelve milestones, `M0` … `M11`. Each was verified before the next began: 332 tests plus 3
live tests, ruff, mypy `--strict`, `pip-audit`, `npm audit`, and 4 migrations applied from an
empty schema.

**M0–M5 spent no API tokens.** The clause renderer, the three validation gates, the provenance
chain, the workspace, and the error policy all existed and were tested before the first agent
was written. The agents were then built against gates that already worked.

| | Milestone | What it established |
|---|---|---|
| M0 | Foundation | Layout, config that fails loud, Postgres, CI. Renamed `backend/agents/` → `subagents/`: the SDK's import root is `agents`. |
| M1 | Clause library + `render_clause` | Jinja2 `StrictUndefined` — a missing party name raises rather than rendering blank. Provenance by `clause_id@version` + sha256. |
| M2 | `validate_draft` | Completeness, placeholders, fidelity. Any blocker caps the score at 89. |
| M3 | Workspace store | Postgres VFS. `clauses/` read-only, enforced in the store. 20 concurrent writers, no lost updates. |
| M4 | Error policy | Return vs. raise. The SDK has no `ToolError`. A leaky formatter would echo SQL and DSNs to the model. |
| M5 | Tool registry | Ten tools; loop detection by canonicalised argument hash. |
| M6 | Sub-agents | Drafting and Judge as agents-as-tools. Isolation asserted on captured bytes. |
| M7 | Orchestrator | Suspend/resume, budgets rehydrated from the ledger. **The riskiest milestone.** |
| M8 | `finalize_contract` | The choke point. A clean 85 beats a blocked 88. |
| M9 | DOCX export | Byte-stable. Only a finalized version id can become a document. |
| M10 | FastAPI + SSE | Persist before notify, mutation-tested. Replay from `?seq=N`. |
| M11 | Next.js UI | Live plan, tool trace, question form, download. |

### What the Phase 1 exit criteria asked for

- [x] Free-text request → watermarked DOCX, no fixed prompt template.
- [x] Missing information is asked for, never invented. The live orchestrator asked four
      questions and drafted nothing until they were answered.
- [x] 100% of clauses traceable to `clause_id@version`, including an appendix in the document.
- [x] The refinement loop exists and is observable in the event stream.
- [x] An unsupported contract type is declined, not improvised.
- [x] A new capability is a **tool**, not an orchestrator branch.
- [ ] **p95 latency and cost per contract are not yet measured.** Single live runs completed
      comfortably inside 90 s and cost ~25,000 tokens end to end. That is an observation, not a
      p95. See "Before production" below.

### Things the plan did not anticipate

Recorded because each cost real time, and each would otherwise be rediscovered:

- `openai 2.45.0` broke `agents.usage.Usage()` on `openai-agents 0.18.0` — a pairing the
  declared constraint permits. Every agent run would have failed. Now pinned, with a canary test.
- `alembic --autogenerate` wanted to **drop the SDK's session tables**, deleting every
  contract's conversation history.
- `SuspendRun` arrives wrapped in `UserError`, and there is **no orphaned tool call** — the
  plan's central risk did not exist, and the planned fix would have caused it.
- `render_clauses` discarded `contract_type`, so every post-suspension slice failed. Invisible
  to the fakes; found by one live run.
- The fidelity gate tolerated `"strict confidence"` → `"reasonable confidence"`.
- Next bakes `rewrites()` into the build, not into `next start`.

---

## Before production

None of these are Phase 2. They are the gap between "works" and "deployable".

- **Measure p95 latency and tokens per contract** over a real sample. The PRD's targets
  (< 90 s, < $0.40) are unverified.
- **Auth and multi-tenancy.** Single-tenant, no auth, by decision. The schema carries no
  `tenant_id`; retrofitting means a migration across every table.
- **Object storage.** `LocalStorage` writes to disk. `Storage` is a protocol; S3 is a class.
- **`LISTEN/NOTIFY` or Redis** for the event doorbell. The in-process `asyncio.Queue` is correct
  only while the agent task and the SSE handler share a process. DB replay covers the gap, so
  this is a latency fix, not a correctness one.
- **Rendering verification.** The DOCX was validated by reopening it with `python-docx`. It has
  not been opened in Word or LibreOffice.
- **A second jurisdiction.** The library assumes Indian law. `jurisdiction` is already an axis
  in the loader and the frontmatter.

---

## Phase 2 — Memory

**Goal:** the second contract asks at least 40% fewer questions than the first.

Every one of these is a **tool** the orchestrator gains, or a service behind one. None touches
`deep_agent.py`. That is the extensibility claim, and Phase 1 was built to make it cheap.

- **M12 — Memory store.** `memory_facts`, `company_profiles`, `conversations` + migration.
  *Done when:* a fact learned in one contract is present in the next.
- **M13 — `recall_memory` / `remember_fact` tools.** Questions the orchestrator can answer from
  memory are never asked; the response says which values came from memory.
  *Done when:* contract 2 for the same user asks strictly fewer questions than contract 1.
- **M14 — "Use your defaults."** Applies company-profile defaults, enumerates exactly which,
  and marks those fields for reviewer attention.
- **M15 — Conversation summarization.** One model call compresses old turns.
  *Done when:* a 20-turn conversation stays inside the context budget and every learned fact
  survives, asserted against `memory_facts`.

---

## Phase 3 — already shipped

The original Phase 3 was the LLM Judge and the refinement loop. Both landed in Phase 1 (M6, M7),
composed with the deterministic gates so a blocked draft never reaches the model.

What remains of the original intent:

- **Rubric calibration.** Fixture drafts with known defects; assert the judge's scores fall in
  expected bands. Guard against score inflation across attempts. The structural guard already
  exists — the judge never sees its own prior score — but the correlation with human rating is
  unmeasured.

---

## Phase 4 — Integration

- **M16 — Tool registry over MCP.** Expose the existing tools; register external MCP servers.
- **M17 — Company policy retrieval.** Policies as a second retrieval source, behind the same
  read-only workspace prefix.
- **M18 — Vector retrieval.** *Gated on library size (~200 clauses) or free-text search over
  prior contracts — not on fashion.* Today, exact filtered lookup over thirteen files is faster,
  free, reproducible, and more accurate than embedding search.
- **M19 — Drive / SharePoint connectors.** Read-only ingestion of prior contracts.

---

## Phase 5 — Multi-agent

Risk Analysis · Negotiation · Compliance · Contract Comparison · Approval Workflow.

Each is a **new sub-agent registered as a tool**. The orchestrator's system prompt gains a line;
its code does not change. Where a genuinely dynamic loop is needed — negotiation and compliance
iterating an unknown number of times — that subgraph gets its own runner, scoped to the subgraph,
with its own turn cap.

The invariants do not move. Whatever is added, `finalize_contract` remains the only path to a
contract and `export_docx` accepts only its output.
