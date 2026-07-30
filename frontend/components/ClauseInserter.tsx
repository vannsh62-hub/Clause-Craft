"use client";

import { useEffect, useState } from "react";
import { getClauseText, listClauses, renderClauseForContract, type ClauseSummary } from "@/lib/api";

/**
 * Browse the approved clause library and insert a clause into the draft.
 *
 * The clauses come from the same library the drafting agent draws on. Inserting one drops
 * its approved text into the document as a new section, so a user can add a clause the
 * agent did not — from the same vetted source, not free-typed.
 *
 * When `contractId` is supplied, the clause is rendered with this contract's own values —
 * receiving party, disclosing party, dates, and so on — pulled from what the drafting run
 * already collected, so nothing needs to be typed in again. A variable this contract never
 * resolved still shows as `{{ name }}`, a visible reminder rather than a silent blank.
 */
export function ClauseInserter({
  contractId,
  onInsert,
  onClose,
}: {
  contractId?: string;
  onInsert: (markdown: string) => void;
  onClose: () => void;
}) {
  const [clauses, setClauses] = useState<ClauseSummary[]>([]);
  const [status, setStatus] = useState<"loading" | "ready">("loading");
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [insertingId, setInsertingId] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    listClauses()
      .then((rows) => {
        if (!live) return;
        setClauses(rows);
        setStatus("ready");
      })
      .catch((err) => {
        if (!live) return;
        setError(err instanceof Error ? err.message : String(err));
        setStatus("ready");
      });
    return () => {
      live = false;
    };
  }, []);

  async function insert(clause: ClauseSummary) {
    setInsertingId(clause.id);
    setError(null);
    try {
      if (contractId) {
        // Auto-filled from this contract's own values — no re-typing party names or dates.
        const rendered = await renderClauseForContract(contractId, clause.id);
        onInsert(`\n\n## ${rendered.title}\n\n${rendered.text}\n`);
      } else {
        const text = await getClauseText(clause.id);
        // The approved template body, as its own section. Unfilled variables stay as `{{ }}`
        // and are caught by the placeholder gate — a visible reminder, not a silent blank.
        onInsert(`\n\n## ${text.title}\n\n${text.body}\n`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setInsertingId(null);
    }
  }

  const shown = clauses.filter((c) => {
    const q = filter.trim().toLowerCase();
    if (!q) return true;
    return (
      c.title.toLowerCase().includes(q) ||
      c.id.toLowerCase().includes(q) ||
      c.contract_type.toLowerCase().includes(q)
    );
  });

  return (
    <div className="clause-picker">
      <div className="clause-picker-header">
        <input
          type="text"
          className="clause-picker-search"
          placeholder="Search the clause library…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <button type="button" className="clause-picker-close" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>

      {status === "loading" ? (
        <p className="p-4 text-sm text-[color:var(--text-muted)]">Loading library…</p>
      ) : error ? (
        <p className="p-4 text-sm text-rose-600">{error}</p>
      ) : (
        <ul className="clause-picker-list">
          {shown.map((clause) => (
            <li key={clause.id} className="clause-picker-item">
              <div className="min-w-0">
                <div className="text-sm font-semibold text-[color:var(--text)]">{clause.title}</div>
                <div className="mt-0.5 flex flex-wrap gap-2 text-xs text-[color:var(--text-muted)]">
                  <span>{clause.id}</span>
                  <span>• {clause.contract_type}</span>
                  {clause.required && <span>• required</span>}
                </div>
              </div>
              <button
                type="button"
                className="clause-picker-insert"
                onClick={() => insert(clause)}
                disabled={insertingId === clause.id}
              >
                {insertingId === clause.id ? "Inserting…" : "Insert"}
              </button>
            </li>
          ))}
          {shown.length === 0 && (
            <li className="p-4 text-sm text-[color:var(--text-muted)]">No clauses match.</li>
          )}
        </ul>
      )}
    </div>
  );
}
