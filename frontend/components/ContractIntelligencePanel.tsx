// Contract Intelligence: one shared `ContractAnalysis` (fetched by the parent, passed in)
// powers the Health Score card, Document Outline, Risk Heatmap coloring (consumed by the
// parent for clause borders — see `riskColor` export), AI Explain popover, and per-clause
// AI Clause Suggestions. Never fetches per-widget: the parent debounces one fetch per
// document version and this component only renders slices of that single result.
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

export function ContractIntelligencePanel({
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
  const [explainTitle, setExplainTitle] = useState<string | null>(null);
  const [dismissedSuggestions, setDismissedSuggestions] = useState<Set<string>>(new Set());
  const [dismissedMissing, setDismissedMissing] = useState<Set<string>>(new Set());
  const [outlineOpen, setOutlineOpen] = useState(true);

  if (!analysis && !loading) return null;

  const explainClause = analysis?.clauses.find((c) => c.title === explainTitle) ?? null;
  const missingClauses = (analysis?.missing_clauses ?? []).filter((t) => !dismissedMissing.has(t));

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[220px_1fr] gap-4 mt-8 max-w-6xl mx-auto items-start">
      {/* Document Outline — permanent left sidebar */}
      <div className="glass-panel overflow-hidden lg:sticky lg:top-4">
        <button
          type="button"
          onClick={() => setOutlineOpen((v) => !v)}
          className="w-full flex items-center justify-between px-4 py-3 border-b border-[color:var(--border)] text-xs font-semibold text-[color:var(--text)]"
        >
          Contract Outline
          <span className="text-[color:var(--text-muted)]">{outlineOpen ? "▾" : "▸"}</span>
        </button>
        {outlineOpen && (
          <ul className="py-2 max-h-[60vh] overflow-y-auto">
            {(analysis?.clauses ?? []).map((c) => (
              <li key={c.title}>
                <button
                  type="button"
                  onClick={() => onScrollToClause(c.title)}
                  className="w-full flex items-center gap-2 px-4 py-1.5 text-left text-xs hover:bg-[color:var(--surface-strong)] transition-colors"
                  title={c.risk_reason}
                >
                  <span
                    className="inline-block w-2 h-2 rounded-full flex-shrink-0"
                    style={{ background: riskColor(c.risk) }}
                  />
                  <span className="truncate text-[color:var(--text)]">{c.title}</span>
                  {c.variables_unresolved.length > 0 && (
                    <span className="ml-auto text-[10px] font-semibold text-[color:var(--accent-strong)]">
                      {c.variables_unresolved.length}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex flex-col gap-4">
        {/* Smart Contract Health Score */}
        <div className="glass-panel p-4">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div className="flex items-center gap-3">
              <span className="text-2xl font-bold" style={{ color: analysis ? healthColor(analysis.health_score) : undefined }}>
                {loading ? "…" : `${analysis?.health_score ?? 0} / 100`}
              </span>
              <span className="text-xs font-semibold text-[color:var(--text-muted)]">Contract Health</span>
            </div>
            {/* Negotiation Mode toggle */}
            <div className="flex items-center gap-1 rounded-full border border-[color:var(--border)] p-0.5">
              {(["vendor", "neutral", "client"] as NegotiationPerspective[]).map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => onPerspectiveChange(p)}
                  className={`text-[11px] font-semibold px-2.5 py-1 rounded-full capitalize transition-colors ${
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

          <ul className="mt-3 space-y-1">
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
                <div key={title} className="flex items-center justify-between gap-2 text-xs">
                  <span className="text-amber-700">⚠ {title} clause missing</span>
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => onGenerateMissingClause?.(title)}
                      className="text-[11px] font-semibold px-2 py-0.5 rounded-full border border-[color:var(--accent-soft)] text-[color:var(--accent-strong)]"
                    >
                      AI Generate
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
        </div>

        {/* AI Clause Suggestions */}
        {(analysis?.clauses ?? []).some((c) => c.suggestions.length > 0) && (
          <div className="glass-panel p-4">
            <h4 className="text-xs font-semibold text-[color:var(--text-muted)] mb-2">AI Clause Suggestions</h4>
            <div className="space-y-2">
              {(analysis?.clauses ?? []).flatMap((c) =>
                c.suggestions.map((s, i) => {
                  const key = `${c.title}:${i}`;
                  if (dismissedSuggestions.has(key)) return null;
                  return (
                    <div key={key} className="rounded-xl border border-[color:var(--border)] p-3">
                      <div className="text-[11px] font-semibold text-[color:var(--text-muted)]">{c.title}</div>
                      <div className="text-xs mt-1 text-[color:var(--text)]">💡 {s.text}</div>
                      {s.rationale && <div className="text-[11px] mt-0.5 text-[color:var(--text-muted)]">{s.rationale}</div>}
                      <div className="flex gap-2 mt-2">
                        <button
                          type="button"
                          onClick={() => onScrollToClause(c.title)}
                          className="text-[11px] font-semibold px-2 py-0.5 rounded-full border border-[color:var(--accent-soft)] text-[color:var(--accent-strong)]"
                        >
                          Review clause
                        </button>
                        <button
                          type="button"
                          onClick={() => setDismissedSuggestions((prev) => new Set(prev).add(key))}
                          className="text-[11px] font-semibold px-2 py-0.5 rounded-full border border-[color:var(--border)] text-[color:var(--text-muted)]"
                        >
                          Dismiss
                        </button>
                      </div>
                    </div>
                  );
                }),
              )}
            </div>
          </div>
        )}
      </div>

      {/* AI Explain popover */}
      {explainClause && (
        <div
          className="fixed inset-0 z-50 flex justify-end bg-black/20"
          onClick={() => setExplainTitle(null)}
        >
          <div
            className="w-full max-w-sm h-full bg-[color:var(--surface)] shadow-xl overflow-y-auto p-5"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold text-sm">{explainClause.title}</h3>
              <button type="button" onClick={() => setExplainTitle(null)} className="text-[color:var(--text-muted)]">
                ✕
              </button>
            </div>
            <ExplainSection label="Summary" text={explainClause.summary} />
            <ExplainSection label="Plain English" text={explainClause.plain_english} />
            <ExplainSection label="Business Purpose" text={explainClause.business_purpose} />
            <div className="flex items-center gap-2 my-2">
              <span
                className="inline-block w-2.5 h-2.5 rounded-full"
                style={{ background: riskColor(explainClause.risk) }}
              />
              <span className="text-xs font-semibold capitalize">{explainClause.risk} risk</span>
            </div>
            {explainClause.risk_reason && <p className="text-xs text-[color:var(--text-muted)] mb-2">{explainClause.risk_reason}</p>}
            <ExplainSection label="Negotiation Tips" text={explainClause.negotiation_tips} />
            <ExplainSection label="Common Alternatives" text={explainClause.common_alternatives} />
            <ExplainSection label="Potential Problems" text={explainClause.potential_problems} />
            {(explainClause.depends_on.length > 0 || explainClause.referenced_by.length > 0 || explainClause.cross_references.length > 0) && (
              <div className="mt-3 pt-3 border-t border-[color:var(--border)] text-xs space-y-1">
                {explainClause.depends_on.length > 0 && <div>Depends on: {explainClause.depends_on.join(", ")}</div>}
                {explainClause.referenced_by.length > 0 && <div>Referenced by: {explainClause.referenced_by.join(", ")}</div>}
                {explainClause.cross_references.length > 0 && <div>Cross references: {explainClause.cross_references.join(", ")}</div>}
              </div>
            )}
            <div className="mt-3 pt-3 border-t border-[color:var(--border)] grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-[color:var(--text-muted)]">
              <span>Words: {explainClause.analytics.words}</span>
              <span>Reading time: {explainClause.analytics.reading_time_seconds}s</span>
              <span>Readability: {explainClause.analytics.readability}</span>
              <span>Variables: {explainClause.analytics.variables}</span>
              <span>Cross references: {explainClause.analytics.cross_references}</span>
              <span>AI suggestions: {explainClause.analytics.ai_suggestions}</span>
            </div>
          </div>
        </div>
      )}

      {/* Exposed imperatively via data attribute hook for the toolbar's "✨ Explain" button */}
      <ExplainOpenBridge onOpen={setExplainTitle} />
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

// Lets the existing floating clause toolbar (in page.tsx) open the Explain popover for a
// clause by title without this component needing to be threaded through every toolbar
// render — it listens for a custom event instead of prop drilling into the toolbar.
function ExplainOpenBridge({ onOpen }: { onOpen: (title: string) => void }) {
  useEffect(() => {
    const listener = (e: Event) => onOpen((e as CustomEvent<string>).detail);
    window.addEventListener("contract-intelligence:explain", listener);
    return () => window.removeEventListener("contract-intelligence:explain", listener);
  }, [onOpen]);
  return null;
}

/** Dispatch to open the Explain popover for `title` — called from page.tsx's clause toolbar. */
export function openClauseExplain(title: string) {
  window.dispatchEvent(new CustomEvent("contract-intelligence:explain", { detail: title }));
}
