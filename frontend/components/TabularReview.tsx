"use client";

import { useMemo, useState } from "react";
import { useUiChrome } from "@/components/UiChrome";
import type { ContractSummary } from "@/lib/api";

/**
 * Tabular Review — every contract as a reviewable grid.
 *
 * One row per contract; columns are the attributes you'd scan across a portfolio (type,
 * status, finalized, date). Reads the same contract list the sidebar history uses. Clicking a
 * row opens that contract's draft, so the table is a way in, not a dead end.
 */

function titleOf(request: string): string {
  const first = request.trim().split("\n")[0];
  return first.length > 70 ? `${first.slice(0, 70)}…` : first || "Untitled draft";
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function TabularReview({ onClose }: { onClose: () => void }) {
  const { contracts, openContract } = useUiChrome();
  const [filter, setFilter] = useState("");

  const rows = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return contracts;
    return contracts.filter((c) =>
      [c.request, c.contract_type ?? "", c.status].join(" ").toLowerCase().includes(q),
    );
  }, [contracts, filter]);

  function open(c: ContractSummary) {
    openContract(c);
    onClose();
  }

  return (
    <div className="playbook-overlay" role="dialog" aria-modal="true" aria-label="Tabular review">
      <div className="library-modal">
        <div className="playbook-modal-header">
          <div>
            <h3 className="text-base font-semibold text-[color:var(--text)]">Tabular Review</h3>
            <p className="mt-0.5 text-xs text-[color:var(--text-muted)]">
              {contracts.length} contract{contracts.length === 1 ? "" : "s"}. Click a row to open
              its draft.
            </p>
          </div>
          <button type="button" className="playbook-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="library-toolbar">
          <input
            type="text"
            className="clause-picker-search flex-1"
            placeholder="Search by request, type, or status…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>

        <div className="library-table-wrap">
          {rows.length === 0 ? (
            <p className="px-1 py-6 text-sm text-[color:var(--text-muted)]">
              {contracts.length === 0 ? "No contracts yet." : "Nothing matches your search."}
            </p>
          ) : (
            <table className="library-table">
              <thead>
                <tr>
                  <th>Contract</th>
                  <th>Type</th>
                  <th>Status</th>
                  <th>Stage</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => (
                  <tr
                    key={c.id}
                    onClick={() => open(c)}
                    style={{ cursor: "pointer" }}
                    title="Open this contract"
                  >
                    <td>{titleOf(c.request)}</td>
                    <td>{c.contract_type ?? "—"}</td>
                    <td>{c.status}</td>
                    <td>
                      <span
                        className={`clause-risk ${c.finalized ? "clause-risk-low" : "clause-risk-medium"}`}
                      >
                        {c.finalized ? "Finalized" : "Draft"}
                      </span>
                    </td>
                    <td>{formatDate(c.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
