// Contract Intelligence Workspace. One shared `ContractAnalysis` (fetched by the parent,
// passed in) powers every piece here: the left Outline sidebar, the right Intelligence
// sidebar (health ring, negotiation mode, warnings, relationships graph), the inline
// per-clause suggestion cards + analytics footers rendered directly under each clause in
// the center editor, and the Explain side panel. Nothing here fetches on its own.
"use client";

import { useEffect, useState } from "react";
import type { ClauseAnalysis, ContractAnalysis, NegotiationPerspective } from "@/lib/api";

export function riskColor(risk: string | undefined): string {
  if (risk === "high") return "#dc2626";
  if (risk === "low") return "#16a34a";
  return "#d97706"; // medium
}

function healthColor(score: number): string {
  if (score >= 90) return "#16a34a";
  if (score >= 70) return "#d97706";
  return "#dc2626";
}

/* ------------------------------------------------------------------ Left: Outline */

export function ContractOutlineSidebar({
  analysis,
  onScrollToClause,
}: {
  analysis: ContractAnalysis | null;
  onScrollToClause: (title: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(true);
  const clauses = (analysis?.clauses ?? []).filter((c) =>
    c.title.toLowerCase().includes(query.toLowerCase()),
  );

  return (
    <div className="glass-panel overflow-hidden lg:sticky lg:top-4 lg:self-start">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-4 py-3 border-b border-[color:var(--border)] text-xs font-semibold text-[color:var(--text)]"
      >
        📄 Contract Outline
        <span className="text-[color:var(--text-muted)]">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <>
          <div className="px-3 pt-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search clauses…"
              className="w-full text-xs px-2.5 py-1.5 rounded-full border border-[color:var(--border)] bg-[color:var(--surface)] outline-none focus:border-[color:var(--accent-soft)]"
            />
          </div>
          <ul className="py-2 max-h-[65vh] overflow-y-auto">
            {clauses.map((c) => (
              <li key={c.title}>
                <button
                  type="button"
                  onClick={() => onScrollToClause(c.title)}
                  className="w-full flex items-center gap-2 px-4 py-1.5 text-left text-xs hover:bg-[color:var(--surface-strong)] transition-colors"
                  title={c.risk_reason}
                >
                  <span className="inline-block w-2 h-2 rounded-full flex-shrink-0" style={{ background: riskColor(c.risk) }} />
                  <span className="truncate text-[color:var(--text)]">▶ {c.title}</span>
                  {c.variables_unresolved.length > 0 && (
                    <span className="ml-auto text-[10px] font-semibold text-[color:var(--accent-strong)]">
                      {c.variables_unresolved.length}
                    </span>
                  )}
                </button>
              </li>
            ))}
            {clauses.length === 0 && (
              <li className="px-4 py-2 text-xs text-[color:var(--text-muted)]">
                {analysis ? "No clauses match." : "Analyzing…"}
              </li>
            )}
          </ul>
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ Right: Intelligence sidebar */

function ScoreRing({ score }: { score: number }) {
  const r = 30;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, score)) / 100;
  return (
    <svg width="76" height="76" viewBox="0 0 76 76" className="flex-shrink-0">
      <circle cx="38" cy="38" r={r} fill="none" stroke="var(--border)" strokeWidth="7" />
      <circle
        cx="38"
        cy="38"
        r={r}
        fill="none"
        stroke={healthColor(score)}
        strokeWidth="7"
        strokeLinecap="round"
        strokeDasharray={`${c * pct} ${c}`}
        transform="rotate(-90 38 38)"
      />
      <text x="38" y="42" textAnchor="middle" fontSize="18" fontWeight="700" fill="var(--text)">
        {score}
      </text>
    </svg>
  );
}

export function ContractIntelligenceSidebar({
  analysis,
  loading,
  perspective,
  onPerspectiveChange,
  onScrollToClause,
  onDismissMissingClause,
  onGenerateMissingClause,
}: {
  analysis: ContractAnalysis | null;
  loading: boolean;
  perspective: NegotiationPerspective;
  onPerspectiveChange: (p: NegotiationPerspective) => void;
  onScrollToClause: (title: string) => void;
  onDismissMissingClause?: (title: string) => void;
  onGenerateMissingClause?: (title: string) => void;
}) {
  const [tab, setTab] = useState<"overview" | "relationships">("overview");
  const [dismissedMissing, setDismissedMissing] = useState<Set<string>>(new Set());
  const missingClauses = (analysis?.missing_clauses ?? []).filter((t) => !dismissedMissing.has(t));

  return (
    <div className="glass-panel overflow-hidden lg:sticky lg:top-4 lg:self-start">
      <div className="px-4 py-3 border-b border-[color:var(--border)] flex items-center justify-between">
        <h3 className="text-xs font-semibold text-[color:var(--text)]">Contract Intelligence</h3>
      </div>

      {/* Negotiation Mode */}
      <div className="px-4 pt-3 flex items-center justify-center gap-1 rounded-full">
        <div className="flex items-center gap-1 rounded-full border border-[color:var(--border)] p-0.5 w-full justify-center">
          {(["vendor", "neutral", "client"] as NegotiationPerspective[]).map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => onPerspectiveChange(p)}
              className={`text-[11px] font-semibold px-2.5 py-1 rounded-full capitalize transition-colors flex-1 ${
                perspective === p
                  ? "bg-[color:var(--accent-soft)] text-[color:var(--accent-strong)]"
                  : "text-[color:var(--text-muted)] hover:bg-[color:var(--surface-strong)]"
              }`}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      {/* Contract Score */}
      <div className="px-4 py-4 flex items-center gap-3">
        <ScoreRing score={loading && !analysis ? 0 : analysis?.health_score ?? 0} />
        <div>
          <div className="text-lg font-bold" style={{ color: analysis ? healthColor(analysis.health_score) : undefined }}>
            {loading && !analysis ? "…" : `${analysis?.health_score ?? 0} / 100`}
          </div>
          <div className="text-[11px] text-[color:var(--text-muted)]">Contract Score</div>
        </div>
      </div>

      {/* Tabs */}
      <div className="px-4 flex gap-1 border-b border-[color:var(--border)]">
        {(["overview", "relationships"] as const).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={`text-[11px] font-semibold px-2.5 py-1.5 capitalize border-b-2 -mb-px transition-colors ${
              tab === t ? "border-[color:var(--accent)] text-[color:var(--accent-strong)]" : "border-transparent text-[color:var(--text-muted)]"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      <div className="px-4 py-3 max-h-[65vh] overflow-y-auto">
        {tab === "overview" ? (
          <>
            <ul className="space-y-1">
              {(analysis?.findings ?? []).map((f, i) => (
                <li
                  key={i}
                  onClick={() => f.clause_title && onScrollToClause(f.clause_title)}
                  className={`text-xs flex items-center gap-1.5 ${f.clause_title ? "cursor-pointer hover:underline" : ""} ${
                    f.ok ? "text-[color:var(--text)]" : "text-amber-700"
                  }`}
                >
                  <span>{f.ok ? "✓" : "⚠"}</span>
                  {f.text}
                </li>
              ))}
            </ul>

            {missingClauses.length > 0 && (
              <div className="mt-3 pt-3 border-t border-[color:var(--border)] space-y-2">
                {missingClauses.map((title) => (
                  <div key={title} className="text-xs space-y-1">
                    <span className="text-amber-700">⚠ Missing {title}</span>
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => onGenerateMissingClause?.(title)}
                        className="text-[11px] font-semibold px-2 py-0.5 rounded-full border border-[color:var(--accent-soft)] text-[color:var(--accent-strong)]"
                      >
                        Generate
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setDismissedMissing((prev) => new Set(prev).add(title));
                          onDismissMissingClause?.(title);
                        }}
                        className="text-[11px] font-semibold px-2 py-0.5 rounded-full border border-[color:var(--border)] text-[color:var(--text-muted)]"
                      >
                        Dismiss
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {(analysis?.clauses ?? []).some((c) => c.suggestions.length > 0) && (
              <div className="mt-3 pt-3 border-t border-[color:var(--border)]">
                <div className="text-[11px] font-semibold text-[color:var(--text-muted)] mb-1.5">Suggestions</div>
                <div className="space-y-1.5">
                  {(analysis?.clauses ?? []).flatMap((c) =>
                    c.suggestions.map((s, i) => (
                      <button
                        key={`${c.title}:${i}`}
                        type="button"
                        onClick={() => onScrollToClause(c.title)}
                        className="w-full text-left text-[11px] rounded-lg border border-[color:var(--border)] px-2 py-1.5 hover:bg-[color:var(--surface-strong)]"
                      >
                        <span className="font-semibold">{c.title}: </span>
                        {s.text}
                      </button>
                    )),
                  )}
                </div>
              </div>
            )}
          </>
        ) : (
          <RelationshipsGraph analysis={analysis} onScrollToClause={onScrollToClause} />
        )}
      </div>
    </div>
  );
}

function RelationshipsGraph({
  analysis,
  onScrollToClause,
}: {
  analysis: ContractAnalysis | null;
  onScrollToClause: (title: string) => void;
}) {
  const clauses = (analysis?.clauses ?? []).filter(
    (c) => c.depends_on.length > 0 || c.referenced_by.length > 0 || c.cross_references.length > 0,
  );
  if (clauses.length === 0) {
    return <p className="text-xs text-[color:var(--text-muted)]">No clause relationships detected yet.</p>;
  }
  return (
    <div className="space-y-3">
      {clauses.map((c) => (
        <div key={c.title} className="text-xs">
          <button type="button" onClick={() => onScrollToClause(c.title)} className="font-semibold hover:underline">
            {c.title}
          </button>
          {c.depends_on.map((t) => (
            <div key={`dep-${t}`} className="pl-3 text-[color:var(--text-muted)]">
              ↓ depends on{" "}
              <button type="button" onClick={() => onScrollToClause(t)} className="hover:underline text-[color:var(--text)]">
                {t}
              </button>
            </div>
          ))}
          {c.referenced_by.map((t) => (
            <div key={`ref-${t}`} className="pl-3 text-[color:var(--text-muted)]">
              ↑ referenced by{" "}
              <button type="button" onClick={() => onScrollToClause(t)} className="hover:underline text-[color:var(--text)]">
                {t}
              </button>
            </div>
          ))}
          {c.cross_references.map((t) => (
            <div key={`xref-${t}`} className="pl-3 text-[color:var(--text-muted)]">
              ↔ references{" "}
              <button type="button" onClick={() => onScrollToClause(t)} className="hover:underline text-[color:var(--text)]">
                {t}
              </button>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ Center: inline widgets */

/** Rendered directly under a clause's markdown in the editor — visible without opening
 * AI Edit or any popover. */
export function ClauseSuggestionCards({ clause }: { clause: ClauseAnalysis | undefined }) {
  const [dismissed, setDismissed] = useState<Set<number>>(new Set());
  if (!clause || clause.suggestions.length === 0) return null;
  return (
    <div className="mt-2 space-y-1.5">
      {clause.suggestions.map((s, i) =>
        dismissed.has(i) ? null : (
          <div key={i} className="rounded-xl border border-[color:var(--accent-soft)] bg-[rgba(15,118,110,0.04)] px-3 py-2 text-xs">
            <div className="font-semibold text-[color:var(--accent-strong)]">💡 AI Suggestion</div>
            <div className="mt-0.5 text-[color:var(--text)]">{s.text}</div>
            <div className="flex gap-2 mt-1.5">
              <button
                type="button"
                onClick={() => setDismissed((prev) => new Set(prev).add(i))}
                className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-[color:var(--accent-soft)] text-[color:var(--accent-strong)]"
              >
                Accept
              </button>
              <button
                type="button"
                onClick={() => setDismissed((prev) => new Set(prev).add(i))}
                className="text-[11px] font-semibold px-2 py-0.5 rounded-full border border-[color:var(--border)] text-[color:var(--text-muted)]"
              >
                Dismiss
              </button>
            </div>
          </div>
        ),
      )}
    </div>
  );
}

/** Rendered at the bottom of every clause. */
export function ClauseAnalyticsFooter({ clause }: { clause: ClauseAnalysis | undefined }) {
  if (!clause) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-[color:var(--text-muted)]">
      <span
        className="inline-flex items-center gap-1 font-semibold capitalize"
        style={{ color: riskColor(clause.risk) }}
        title={clause.risk_reason}
      >
        ● {clause.risk} risk
      </span>
      <span>{clause.analytics.words} words</span>
      <span>{clause.analytics.reading_time_seconds}s read</span>
      <span>{clause.analytics.readability} readability</span>
      <span>{clause.analytics.variables} variables</span>
      <span>{clause.analytics.cross_references} references</span>
      <span>{clause.analytics.ai_suggestions} suggestions</span>
    </div>
  );
}

/* ------------------------------------------------------------------ AI Explain popover */

export function ClauseExplainPopover({ analysis }: { analysis: ContractAnalysis | null }) {
  const [explainTitle, setExplainTitle] = useState<string | null>(null);
  useEffect(() => {
    const listener = (e: Event) => setExplainTitle((e as CustomEvent<string>).detail);
    window.addEventListener("contract-intelligence:explain", listener);
    return () => window.removeEventListener("contract-intelligence:explain", listener);
  }, []);

  const clause = analysis?.clauses.find((c) => c.title === explainTitle) ?? null;
  if (!clause) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/20" onClick={() => setExplainTitle(null)}>
      <div className="w-full max-w-sm h-full bg-[color:var(--surface)] shadow-xl overflow-y-auto p-5" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-sm">{clause.title}</h3>
          <button type="button" onClick={() => setExplainTitle(null)} className="text-[color:var(--text-muted)]">
            ✕
          </button>
        </div>
        <ExplainSection label="Summary" text={clause.summary} />
        <ExplainSection label="Plain English" text={clause.plain_english} />
        <ExplainSection label="Business Purpose" text={clause.business_purpose} />
        <div className="flex items-center gap-2 my-2">
          <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: riskColor(clause.risk) }} />
          <span className="text-xs font-semibold capitalize">{clause.risk} risk</span>
        </div>
        {clause.risk_reason && <p className="text-xs text-[color:var(--text-muted)] mb-2">{clause.risk_reason}</p>}
        <ExplainSection label="Negotiation Tips" text={clause.negotiation_tips} />
        <ExplainSection label="Common Alternatives" text={clause.common_alternatives} />
        <ExplainSection label="Potential Problems" text={clause.potential_problems} />
        {(clause.depends_on.length > 0 || clause.referenced_by.length > 0 || clause.cross_references.length > 0) && (
          <div className="mt-3 pt-3 border-t border-[color:var(--border)] text-xs space-y-1">
            {clause.depends_on.length > 0 && <div>Depends on: {clause.depends_on.join(", ")}</div>}
            {clause.referenced_by.length > 0 && <div>Referenced by: {clause.referenced_by.join(", ")}</div>}
            {clause.cross_references.length > 0 && <div>Cross references: {clause.cross_references.join(", ")}</div>}
          </div>
        )}
        <div className="mt-3 pt-3 border-t border-[color:var(--border)] grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-[color:var(--text-muted)]">
          <span>Words: {clause.analytics.words}</span>
          <span>Reading time: {clause.analytics.reading_time_seconds}s</span>
          <span>Readability: {clause.analytics.readability}</span>
          <span>Variables: {clause.analytics.variables}</span>
          <span>Cross references: {clause.analytics.cross_references}</span>
          <span>AI suggestions: {clause.analytics.ai_suggestions}</span>
        </div>
      </div>
    </div>
  );
}

function ExplainSection({ label, text }: { label: string; text: string }) {
  if (!text) return null;
  return (
    <div className="mb-2">
      <div className="text-[11px] font-semibold text-[color:var(--text-muted)]">{label}</div>
      <div className="text-xs text-[color:var(--text)]">{text}</div>
    </div>
  );
}

/** Dispatch to open the Explain popover for `title` — called from page.tsx's clause toolbar. */
export function openClauseExplain(title: string) {
  window.dispatchEvent(new CustomEvent("contract-intelligence:explain", { detail: title }));
}