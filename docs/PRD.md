# Product Requirements Document — AI Contract Drafting Platform

Version: 1.0
Owner: Vishal Waghmare
Status: Draft for review
Last updated: 2026-07-10

---

## 1. Problem

Drafting a routine commercial contract — an NDA, a service agreement — is high-volume,
low-variance legal work. A junior associate does it by: reading the request, pulling the
firm's approved clause set, filling in the parties and dates, checking nothing is missing,
and handing a clean draft to a senior for review.

Today that loop costs 45–90 minutes of billable time per contract and is the single most
delegated task in a legal team. Naive LLM tools attack it with one prompt and one
completion, which fails in three specific ways:

1. **Hallucinated clauses.** The model invents a governing-law clause when the company
   already has an approved one. Legal cannot accept text of unknown provenance.
2. **Silent omissions.** The model produces a confident-looking NDA with no term/duration
   clause. Nothing flags it. A human catches it, or nobody does.
3. **No memory.** Every draft re-asks the same questions — company name, preferred
   jurisdiction, signatory — because nothing persists between sessions.

## 2. What we are building

A contract drafting platform that reproduces the *workflow* of a junior associate rather
than the *output* of one. The system plans before it writes, retrieves approved clauses
instead of inventing them, asks for information it does not have, reviews its own draft
against a rubric, revises when the draft falls short, and exports a DOCX suitable for
senior legal review.

The unit of value is **a draft a lawyer is willing to redline**, not a draft a lawyer has
to rewrite.

## 3. Explicit non-goals

This product does **not**:

- Give legal advice. Every artifact it emits is a draft for human review.
- Execute, sign, or file contracts. No e-signature integration in any phase of this PRD.
- Replace legal review. The DOCX is watermarked `DRAFT — FOR LEGAL REVIEW` until a human
  clears it.
- Draft contract types outside its clause library. If there is no approved clause set for
  an employment agreement, the system declines rather than improvising one.
- Negotiate against a counterparty. (Phase 5 explores this; it is out of scope for v1.)

This constraint is the product. A tool that will confidently draft anything is a liability;
a tool that refuses outside its approved library is an asset.

## 4. Users

| User | Need | Success looks like |
|---|---|---|
| **In-house counsel** | Turn a one-line request into a reviewable draft | Opens the DOCX and redlines rather than rewrites |
| **Legal ops** | Ensure every draft uses the approved clause set | Can point to the exact clause version in any draft |
| **Business user** (sales, procurement) | Get an NDA out without waiting on legal | Answers 2–3 questions, receives a draft, sends to legal |

The primary user is **in-house counsel**. Legal ops is the buyer. The business user is the
volume driver and the reason the missing-information flow must be forgiving.

## 5. Core principles

These are product commitments, not aspirations. Each maps to a testable behaviour.

| Principle | Concretely means | Verified by |
|---|---|---|
| Think before acting | A plan exists and is inspectable before any draft token is generated | Plan is persisted and returned in the run trace |
| Plan before generating | Missing fields are identified and asked about, not guessed | Draft never contains a fabricated date or party name |
| Use tools, don't hallucinate | Dates, formatting, and document generation are deterministic code | Same inputs → byte-identical DOCX |
| Retrieve, don't memorize | Approved clauses come from the library verbatim | Every clause in the output traces to a `clause_id@version` |
| Review its own work | Every draft is scored against a rubric before release | A `judge_report` exists for every returned contract |
| Learn preferences | The second contract asks fewer questions than the first | Measured: questions asked, draft 1 vs draft 2 |

## 6. Primary user flow — happy path

> **User:** "Draft an NDA between ABC Pvt Ltd and XYZ Pvt Ltd."

1. **Plan.** System classifies this as `draft_contract` / `NDA`, extracts both parties,
   and determines `effective_date` and `duration` are missing.
2. **Ask.** System returns two questions. It does **not** draft yet, and it does **not**
   invent a date. This is the single most important behaviour in the product.
3. **Answer.** User supplies "1 August 2026" and "3 years".
4. **Recall.** System loads the user's company profile — `ABC Pvt Ltd`, Indian law,
   professional register, CEO signatory — and does not re-ask any of it.
5. **Retrieve.** System pulls the approved NDA clause set: definitions, confidentiality,
   obligations, duration, governing law, signatures.
6. **Assemble.** One optimized prompt is built from request + plan + memory + clauses.
7. **Draft.** The model produces Markdown, ordered per the library, using retrieved clause
   text.
8. **Judge.** The draft is scored. Score ≥ 90 → release. Below → feedback returns to the
   drafting step, up to 3 attempts.
9. **Export.** Markdown → DOCX, watermarked, downloadable.

Elapsed target: **under 90 seconds** from step 3 to step 9.

## 7. The flow that actually matters — missing information

Step 2 is where competing tools fail and where this one earns trust. Requirements:

- The system asks for missing fields **before** drafting, never after.
- It asks **only** for fields it cannot obtain from memory or infer safely.
- "Infer safely" means: derivable by deterministic rule (e.g. `term_end = effective_date +
  duration`). It does **not** mean "the model is fairly confident it's a 2-year term."
- A user may answer partially. The system re-asks only what remains.
- A user may say "use your defaults." The system then uses **company-profile defaults**,
  states in the response exactly which defaults it applied, and marks those fields in the
  draft for reviewer attention.
- A user may abandon and return. The partially-filled draft survives.

## 8. Quality bar — what "score ≥ 90" means

A number without a rubric is theatre. The Judge scores six weighted dimensions:

| Dimension | Weight | Fails when |
|---|---|---|
| Completeness | 30 | A required clause for this contract type is absent |
| Placeholder resolution | 25 | Any `{{ variable }}` or `[BRACKET]` survives into the draft |
| Internal consistency | 15 | A defined term is used before definition, or party names drift |
| Clause fidelity | 15 | Retrieved clause text was materially altered |
| Formatting | 10 | Heading hierarchy or numbering is broken |
| Tone & grammar | 5 | Register is informal or text is ungrammatical |

**Completeness** and **placeholder resolution** are hard gates: a draft failing either is
capped below 90 regardless of other scores. They are also checkable *deterministically*,
without an LLM, which is why they carry the most weight (see TDD §Judge).

If three attempts do not reach 90, the system **returns the best-scoring draft anyway**,
flagged `needs_human_review: true` with the outstanding feedback attached. It never returns
nothing, and it never silently returns a failing draft as if it passed.

## 9. Scope by phase

Phase boundaries are drawn so that each phase is independently shippable and each later phase
adds a **tool**, not an orchestrator branch. See [ROADMAP.md](./ROADMAP.md) for milestones.

### Phase 1 — MVP · **shipped**
NDA and Service Agreement. Deep-agent orchestrator, drafting and judge sub-agents, the three
deterministic gates, `finalize_contract`, DOCX export, HTTP + SSE, a minimal UI. Single-turn
memory only.

Two departures from the original scope, both argued in TDD §2. There is **no Planner agent**:
planning is a tool the orchestrator uses (`write_todos`) and revises, not a component that emits
a static plan once. There is **no Context Builder**: it was a pure function pretending to be an
agent. And the **Judge shipped here, not in Phase 3** — a system whose core loop routes every
draft through a judge cannot honestly defer the judge, and deferring it would have made adding
it a core-orchestration change.

### Phase 2 — Memory
User profiles, company profiles, prior-contract recall, long-term preference storage,
conversation summarization. All of it arrives as tools; `deep_agent.py` does not change.

### Phase 3 — Judge · **shipped in Phase 1**
What remains is calibration: correlating the judge's score with human rating on a real sample.

### Phase 4 — Integration
MCP tool integration, company policy retrieval, Google Drive / SharePoint connectors,
multi-document context, vector retrieval (gated on library size, not on fashion — see TDD).

### Phase 5 — Multi-agent
Risk analysis, negotiation, compliance, contract comparison, approval workflow.

## 10. Success criteria (MVP) — **met**

- [x] Understands a free-text contract request with no fixed prompt template.
- [x] Asks for missing information before drafting; never fabricates a date, party, or term.
      A live run asked four questions and drafted nothing until they were answered.
- [x] Assembles clauses from the approved library; every clause is traceable to a
      `clause_id@version`, and the DOCX carries an appendix listing them with checksums.
- [x] Generates a professionally formatted contract in Markdown.
- [x] Produces a DOCX suitable for legal review, watermarked as a draft.
- [x] A new capability is registered as a **tool**, without editing the orchestrator's control
      flow.

The last criterion was originally to be proven by swapping a Phase-1 validator for a Phase-3
LLM judge with no orchestrator diff. That test never ran, because deferring the judge would
itself have been the design error: the composite judge shipped in Phase 1. The claim is instead
carried by the shape of the system — `finalize_contract` and `export_docx` were both added
later as tools, and `deep_agent.py` gained two lines in its tool list and nothing else.

## 11. Metrics

| Metric | Target | Status |
|---|---|---|
| Clauses traceable to library | 100% | **Met.** Enforced by code; a miss is a P0. |
| Judge score on first attempt | ≥ 90 in 80% of runs | Observed 98/100 on live runs. Sample of one; not a rate. |
| Questions asked on 2nd contract vs 1st | ≥ 40% fewer | Phase 2. No memory yet. |
| Draft accepted without structural rewrite | ≥ 70% | **Unmeasured.** Needs real users. |
| p95 end-to-end latency | < 90 s | **Unmeasured.** Single runs finished comfortably inside it. |
| Cost per draft | < $0.40 | **Unmeasured.** ~25,000 tokens end to end; the system budgets in tokens, not currency, because prices move and token counts do not. |

Four of these six are not yet measured. They are listed as targets, not as results.

## 12. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Model alters retrieved clause text | **High** — destroys the legal-provenance guarantee | Clause text is rendered by a template engine, not the LLM. Judge diffs output against source. |
| Prompt injection via party name | **High** — party names are user-controlled and land in the prompt | Variables passed as structured data, never interpolated into instruction text. See TDD §Security. |
| User treats output as legal advice | **High** — liability | Watermark, disclaimer in DOCX, disclaimer in UI, `needs_human_review` in API. |
| Judge score is uncalibrated | Medium — 90 becomes meaningless | Hard deterministic gates carry 55 of 100 points. Rubric is unit-tested against fixture drafts. |
| Vector DB adopted prematurely | Medium — cost and nondeterminism for no gain | Phase 1 library is ~12 documents. Deterministic lookup. Vector search is gated on library size, not on fashion. |
| Contract PII in logs | Medium — compliance | Prompt bodies never logged above DEBUG. |

## 13. Open questions

1. **Jurisdiction scope for MVP.** The clause library assumes Indian law. Do we need a
   second jurisdiction at MVP, or is `jurisdiction` a Phase-2 axis?
2. **Who approves a clause?** The library is "approved" — by what process, and does
   changing a clause require a version bump plus a legal sign-off record? (Assumed yes in
   the TDD data model.)
3. **Multi-tenancy.** Is a "company profile" scoped per tenant, or is the deployment
   single-tenant per customer? This changes the data model's row-level security posture.
4. **Retention.** How long do we hold generated drafts containing counterparty PII?
