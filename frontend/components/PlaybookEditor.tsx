"use client";

import { useEffect, useState } from "react";
import {
  addPlaybookRule,
  deletePlaybookRule,
  getPlaybookRules,
  updatePlaybookRule,
  type PlaybookRule,
  type RuleCondition,
} from "@/lib/api";

/**
 * The playbook, shown as a table of rules — the standing policy a contract must satisfy —
 * with add, edit, and remove.
 *
 * A playbook is not code the user should have to hand-write in YAML. Each rule is: WHEN some
 * facts hold, REQUIRE / FORBID / SET / FLAG something. The table makes that legible, and the
 * form builds one rule at a time. Every save is validated by the same loader the drafting
 * engine uses.
 */

const KINDS: { value: string; label: string }[] = [
  { value: "require_section", label: "Require a section" },
  { value: "forbid_section", label: "Forbid a section" },
  { value: "set_value", label: "Set a value" },
  { value: "flag", label: "Raise a flag" },
];

const OPS = ["eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte", "exists"];
const OP_LABEL: Record<string, string> = {
  eq: "is",
  ne: "is not",
  in: "is one of",
  not_in: "is not one of",
  gt: ">",
  gte: "≥",
  lt: "<",
  lte: "≤",
  exists: "is present",
};

type Draft = PlaybookRule & { originalId: string | null };

const EMPTY: Draft = {
  originalId: null,
  id: "",
  when: [],
  kind: "require_section",
  target: "",
  value: "",
  reason: "",
  blocking: true,
};

function conditionText(c: RuleCondition): string {
  const v = Array.isArray(c.value) ? `[${c.value.join(", ")}]` : String(c.value ?? "");
  if (c.op === "exists") return `${c.field} is present`;
  return `${c.field} ${OP_LABEL[c.op] ?? c.op} ${v}`.trim();
}

function whenText(rule: PlaybookRule): string {
  if (!rule.when || rule.when.length === 0) return "always";
  return rule.when.map(conditionText).join(" and ");
}

function requirementText(rule: PlaybookRule): string {
  switch (rule.kind) {
    case "require_section":
      return `Require a "${rule.target}" section`;
    case "forbid_section":
      return `Forbid a "${rule.target}" section`;
    case "set_value":
      return `Set ${rule.target} = ${rule.value ?? ""}`;
    case "flag":
      return `Flag: ${rule.target}`;
    default:
      return `${rule.kind} ${rule.target}`;
  }
}

export function PlaybookEditor({ onClose }: { onClose: () => void }) {
  const [rules, setRules] = useState<PlaybookRule[]>([]);
  const [explanation, setExplanation] = useState("");
  const [status, setStatus] = useState<"loading" | "ready">("loading");
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let live = true;
    getPlaybookRules()
      .then((view) => {
        if (!live) return;
        setRules(view.rules);
        setExplanation(view.explanation);
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

  async function save() {
    if (!draft) return;
    setSaving(true);
    setError(null);
    try {
      const payload: PlaybookRule = {
        id: draft.id.trim(),
        when: draft.when,
        kind: draft.kind,
        target: draft.target.trim(),
        value: draft.value || null,
        reason: draft.reason || "",
        blocking: draft.blocking ?? true,
      };
      const view = draft.originalId
        ? await updatePlaybookRule(draft.originalId, payload)
        : await addPlaybookRule(payload);
      setRules(view.rules);
      setDraft(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function remove(rule: PlaybookRule) {
    if (!window.confirm(`Remove the rule "${rule.id}"?`)) return;
    setError(null);
    try {
      const view = await deletePlaybookRule(rule.id);
      setRules(view.rules);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="playbook-overlay" role="dialog" aria-modal="true" aria-label="Playbook">
      <div className="library-modal">
        <div className="playbook-modal-header">
          <div>
            <h3 className="text-base font-semibold text-[color:var(--text)]">Playbook</h3>
            <p className="mt-0.5 text-xs text-[color:var(--text-muted)] max-w-2xl">
              {explanation ||
                "Business rules a contract must satisfy."}{" "}
              {rules.length} rule{rules.length === 1 ? "" : "s"}.
            </p>
          </div>
          <button type="button" className="playbook-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        {draft ? (
          <RuleForm
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
              <div className="flex-1 text-xs text-[color:var(--text-muted)]">
                Each rule fires <b>when</b> its conditions hold, then applies its requirement.
              </div>
              <button
                type="button"
                className="playbook-btn-primary whitespace-nowrap"
                onClick={() => {
                  setError(null);
                  setDraft({ ...EMPTY });
                }}
              >
                + New rule
              </button>
            </div>

            {error && <div className="playbook-error">{error}</div>}

            {status === "loading" ? (
              <p className="p-6 text-sm text-[color:var(--text-muted)]">Loading playbook…</p>
            ) : (
              <div className="library-table-wrap">
                <table className="library-table">
                  <thead>
                    <tr>
                      <th>Rule</th>
                      <th>When</th>
                      <th>Requirement</th>
                      <th>Reason</th>
                      <th>Blocking</th>
                      <th className="text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rules.map((r) => (
                      <tr key={r.id}>
                        <td className="font-mono text-xs">{r.id}</td>
                        <td>{whenText(r)}</td>
                        <td>{requirementText(r)}</td>
                        <td className="text-[color:var(--text-muted)]">{r.reason || "—"}</td>
                        <td>
                          <span
                            className={
                              r.blocking === false
                                ? "clause-risk clause-risk-low"
                                : "clause-risk clause-risk-high"
                            }
                          >
                            {r.blocking === false ? "Advisory" : "Blocking"}
                          </span>
                        </td>
                        <td className="text-right whitespace-nowrap">
                          <button
                            type="button"
                            className="library-action"
                            onClick={() =>
                              setDraft({
                                originalId: r.id,
                                id: r.id,
                                when: r.when ?? [],
                                kind: r.kind,
                                target: r.target,
                                value: (r.value as string) ?? "",
                                reason: r.reason ?? "",
                                blocking: r.blocking ?? true,
                              })
                            }
                          >
                            Edit
                          </button>
                          <button
                            type="button"
                            className="library-action library-action-danger"
                            onClick={() => void remove(r)}
                          >
                            Remove
                          </button>
                        </td>
                      </tr>
                    ))}
                    {rules.length === 0 && (
                      <tr>
                        <td colSpan={6} className="p-6 text-center text-[color:var(--text-muted)]">
                          No rules yet. Add one to start enforcing your policy.
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

function RuleForm({
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

  function setCondition(i: number, patch: Partial<RuleCondition>) {
    const when = draft.when.map((c, idx) => (idx === i ? { ...c, ...patch } : c));
    set("when", when);
  }

  return (
    <div className="clause-form">
      <div className="clause-form-grid">
        <label className="clause-field">
          <span>Rule id</span>
          <input
            className="clause-input font-mono"
            value={draft.id}
            disabled={editing}
            placeholder="software-payment-45"
            onChange={(e) => set("id", e.target.value)}
          />
          <small>A short, unique name. Cannot change after creation.</small>
        </label>
        <label className="clause-field">
          <span>Requirement</span>
          <select
            className="clause-input"
            value={draft.kind}
            onChange={(e) => set("kind", e.target.value)}
          >
            {KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </select>
        </label>
        <label className="clause-field">
          <span>Target</span>
          <input
            className="clause-input"
            value={draft.target}
            placeholder={draft.kind === "set_value" ? "payment_terms_days" : "data_protection"}
            onChange={(e) => set("target", e.target.value)}
          />
          <small>
            {draft.kind === "set_value"
              ? "The field to set."
              : draft.kind === "flag"
                ? "What to flag."
                : "The section this rule is about."}
          </small>
        </label>
        {draft.kind === "set_value" && (
          <label className="clause-field">
            <span>Value</span>
            <input
              className="clause-input"
              value={draft.value ?? ""}
              placeholder="45"
              onChange={(e) => set("value", e.target.value)}
            />
          </label>
        )}
        <label className="clause-field clause-field-inline">
          <input
            type="checkbox"
            checked={draft.blocking ?? true}
            onChange={(e) => set("blocking", e.target.checked)}
          />
          <span>Blocking (refuses finalization if unmet)</span>
        </label>
      </div>

      {/* Conditions */}
      <div className="mt-4">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-[color:var(--text-muted)]">
            When (all must hold — leave empty for &quot;always&quot;)
          </span>
          <button
            type="button"
            className="library-action"
            onClick={() => set("when", [...draft.when, { field: "jurisdiction", op: "eq", value: "" }])}
          >
            + Add condition
          </button>
        </div>
        <div className="mt-2 space-y-2">
          {draft.when.map((c, i) => (
            <div key={i} className="condition-row">
              <input
                className="clause-input"
                value={c.field}
                placeholder="jurisdiction"
                onChange={(e) => setCondition(i, { field: e.target.value })}
              />
              <select
                className="clause-input"
                value={c.op}
                onChange={(e) => setCondition(i, { op: e.target.value })}
              >
                {OPS.map((op) => (
                  <option key={op} value={op}>
                    {OP_LABEL[op] ?? op}
                  </option>
                ))}
              </select>
              {c.op !== "exists" && (
                <input
                  className="clause-input"
                  value={
                    Array.isArray(c.value) ? c.value.join(", ") : ((c.value as string) ?? "")
                  }
                  placeholder="IN  (or DE, FR, IE for a list)"
                  onChange={(e) => {
                    const raw = e.target.value;
                    const val =
                      c.op === "in" || c.op === "not_in"
                        ? raw.split(",").map((s) => s.trim()).filter(Boolean)
                        : raw;
                    setCondition(i, { value: val });
                  }}
                />
              )}
              <button
                type="button"
                className="library-action library-action-danger"
                onClick={() => set("when", draft.when.filter((_, idx) => idx !== i))}
              >
                ×
              </button>
            </div>
          ))}
          {draft.when.length === 0 && (
            <p className="text-xs text-[color:var(--text-muted)]">
              No conditions — this rule always applies.
            </p>
          )}
        </div>
      </div>

      <label className="clause-field mt-4">
        <span>Reason</span>
        <input
          className="clause-input"
          value={draft.reason ?? ""}
          placeholder="EU personal data is subject to the GDPR"
          onChange={(e) => set("reason", e.target.value)}
        />
        <small>Shown when this rule blocks a contract, so a reviewer knows why.</small>
      </label>

      {error && <div className="playbook-error">{error}</div>}

      <div className="playbook-modal-footer">
        <span className="text-xs text-[color:var(--text-muted)]">
          A rule states a condition — the language belongs in the clause library.
        </span>
        <div className="flex items-center gap-2">
          <button type="button" className="playbook-btn-ghost" onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className="playbook-btn-primary" onClick={onSave} disabled={saving}>
            {saving ? "Validating…" : editing ? "Save changes" : "Add rule"}
          </button>
        </div>
      </div>
    </div>
  );
}
