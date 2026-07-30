"use client";

import { useEffect, useState } from "react";
import {
  createClause,
  deleteClause,
  getClauseText,
  listClauses,
  updateClause,
  type ClauseSummary,
  type ClauseWrite,
} from "@/lib/api";

/**
 * The clause library, as a table — the catalogue of approved clauses — with add, edit, and
 * remove.
 *
 * Every write goes through the same loader the drafting pipeline uses, so a clause added or
 * edited here is immediately a real, draftable clause, and one that would not load is refused
 * with the reason. Nothing here is a UI-only entry.
 */

type Draft = ClauseWrite & { originalId: string | null };

const EMPTY: Draft = {
  originalId: null,
  id: "nda.",
  title: "",
  contract_type: "nda",
  country: "IN",
  required: false,
  order: 50,
  risk: "medium",
  body: "",
};

const RISK_CLASS: Record<string, string> = {
  low: "clause-risk clause-risk-low",
  medium: "clause-risk clause-risk-medium",
  high: "clause-risk clause-risk-high",
};

export function ClauseLibrary({ onClose }: { onClose: () => void }) {
  const [clauses, setClauses] = useState<ClauseSummary[]>([]);
  const [status, setStatus] = useState<"loading" | "ready">("loading");
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null); // open form (add or edit)
  const [saving, setSaving] = useState(false);

  async function refresh() {
    try {
      setClauses(await listClauses());
      setStatus("ready");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStatus("ready");
    }
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function openEdit(clause: ClauseSummary) {
    // The list carries the body; fall back to fetching it if a row was trimmed.
    let body = clause.body;
    if (!body) {
      try {
        body = (await getClauseText(clause.id)).body;
      } catch {
        body = "";
      }
    }
    setError(null);
    setDraft({
      originalId: clause.id,
      id: clause.id,
      title: clause.title,
      contract_type: clause.contract_type,
      country: clause.country === "Global" ? "" : clause.country,
      required: clause.required,
      order: 50,
      risk: clause.risk,
      body,
    });
  }

  async function save() {
    if (!draft) return;
    setSaving(true);
    setError(null);
    try {
      const payload: ClauseWrite = {
        id: draft.id.trim(),
        title: draft.title,
        contract_type: draft.contract_type,
        country: draft.country,
        required: draft.required,
        order: draft.order,
        risk: draft.risk,
        body: draft.body,
      };
      if (draft.originalId) await updateClause(draft.originalId, payload);
      else await createClause(payload);
      setDraft(null);
      await refresh();
    } catch (err) {
      // The backend refuses an invalid clause with the loader's reason.
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function remove(clause: ClauseSummary) {
    if (!window.confirm(`Remove ${clause.id} from the library?`)) return;
    setError(null);
    try {
      await deleteClause(clause.id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const shown = clauses.filter((c) => {
    const q = filter.trim().toLowerCase();
    if (!q) return true;
    return [c.id, c.category, c.contract_type, c.country, c.risk].some((v) =>
      v.toLowerCase().includes(q),
    );
  });

  return (
    <div className="playbook-overlay" role="dialog" aria-modal="true" aria-label="Clause library">
      <div className="library-modal">
        <div className="playbook-modal-header">
          <div>
            <h3 className="text-base font-semibold text-[color:var(--text)]">Clause Library</h3>
            <p className="mt-0.5 text-xs text-[color:var(--text-muted)]">
              {clauses.length} approved clause{clauses.length === 1 ? "" : "s"}. Add, edit, or
              remove.
            </p>
          </div>
          <button type="button" className="playbook-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        {draft ? (
          <ClauseForm
            draft={draft}
            setDraft={setDraft}
            onCancel={() => setDraft(null)}
            onSave={save}
            saving={saving}
            error={error}
          />
        ) : (
          <>
            <div className="library-toolbar">
              <input
                type="text"
                className="clause-picker-search flex-1"
                placeholder="Search by id, category, type, country, risk…"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              />
              <button
                type="button"
                className="playbook-btn-primary whitespace-nowrap"
                onClick={() => {
                  setError(null);
                  setDraft({ ...EMPTY });
                }}
              >
                + New clause
              </button>
            </div>

            {error && <div className="playbook-error">{error}</div>}

            {status === "loading" ? (
              <p className="p-6 text-sm text-[color:var(--text-muted)]">Loading library…</p>
            ) : (
              <div className="library-table-wrap">
                <table className="library-table">
                  <thead>
                    <tr>
                      <th>Clause ID</th>
                      <th>Category</th>
                      <th>Contract Type</th>
                      <th>Country</th>
                      <th>Version</th>
                      <th>Risk</th>
                      <th>Status</th>
                      <th className="text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((c) => (
                      <tr key={c.id}>
                        <td className="font-mono text-xs">{c.id}</td>
                        <td>{c.category}</td>
                        <td className="uppercase">{c.contract_type}</td>
                        <td>{c.country}</td>
                        <td>v{c.version}</td>
                        <td>
                          <span className={RISK_CLASS[c.risk] ?? "clause-risk"}>
                            {c.risk.charAt(0).toUpperCase() + c.risk.slice(1)}
                          </span>
                        </td>
                        <td>{c.status}</td>
                        <td className="text-right whitespace-nowrap">
                          <button
                            type="button"
                            className="library-action"
                            onClick={() => void openEdit(c)}
                          >
                            Edit
                          </button>
                          <button
                            type="button"
                            className="library-action library-action-danger"
                            onClick={() => void remove(c)}
                          >
                            Remove
                          </button>
                        </td>
                      </tr>
                    ))}
                    {shown.length === 0 && (
                      <tr>
                        <td colSpan={8} className="p-6 text-center text-[color:var(--text-muted)]">
                          No clauses match.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function ClauseForm({
  draft,
  setDraft,
  onCancel,
  onSave,
  saving,
  error,
}: {
  draft: Draft;
  setDraft: (d: Draft) => void;
  onCancel: () => void;
  onSave: () => void;
  saving: boolean;
  error: string | null;
}) {
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft({ ...draft, [k]: v });
  const editing = draft.originalId != null;

  return (
    <div className="clause-form">
      <div className="clause-form-grid">
        <label className="clause-field">
          <span>Clause ID</span>
          <input
            className="clause-input font-mono"
            value={draft.id}
            disabled={editing}
            placeholder="nda.confidentiality"
            onChange={(e) => set("id", e.target.value)}
          />
          <small>Lowercase, {"<type>.<name>"}. Cannot change after creation.</small>
        </label>
        <label className="clause-field">
          <span>Category / Title</span>
          <input
            className="clause-input"
            value={draft.title}
            placeholder="Confidentiality"
            onChange={(e) => set("title", e.target.value)}
          />
        </label>
        <label className="clause-field">
          <span>Contract Type</span>
          <input
            className="clause-input"
            value={draft.contract_type}
            placeholder="nda"
            onChange={(e) => set("contract_type", e.target.value.trim().toLowerCase())}
          />
        </label>
        <label className="clause-field">
          <span>Country</span>
          <input
            className="clause-input"
            value={draft.country}
            placeholder="IN (blank = Global)"
            onChange={(e) => set("country", e.target.value)}
          />
        </label>
        <label className="clause-field">
          <span>Risk</span>
          <select
            className="clause-input"
            value={draft.risk}
            onChange={(e) => set("risk", e.target.value)}
          >
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
        </label>
        <label className="clause-field clause-field-inline">
          <input
            type="checkbox"
            checked={draft.required}
            onChange={(e) => set("required", e.target.checked)}
          />
          <span>Required in this contract type</span>
        </label>
      </div>

      <label className="clause-field mt-3">
        <span>Clause text</span>
        <textarea
          className="clause-input clause-body"
          spellCheck={false}
          value={draft.body}
          placeholder="{{ receiving_party }} shall hold all Confidential Information…"
          onChange={(e) => set("body", e.target.value)}
        />
        <small>
          Use {"{{ variable }}"} placeholders; the variables are derived from the text
          automatically.
        </small>
      </label>

      {error && <div className="playbook-error">{error}</div>}

      <div className="playbook-modal-footer">
        <span className="text-xs text-[color:var(--text-muted)]">
          Saved through the same loader the drafting engine uses.
        </span>
        <div className="flex items-center gap-2">
          <button type="button" className="playbook-btn-ghost" onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className="playbook-btn-primary" onClick={onSave} disabled={saving}>
            {saving ? "Validating…" : editing ? "Save changes" : "Add clause"}
          </button>
        </div>
      </div>
    </div>
  );
}
