"use client";

import { useEffect, useMemo, useState } from "react";
import type { ClauseSection } from "@/lib/clauses";
import { detectPlaceholders, applyPlaceholderValues, type ClauseMissingField } from "@/lib/placeholders";
import { analyseClauseFields, fillClauseFields, updateContractVariables } from "@/lib/api";

/**
 * Fills unresolved variables/placeholders inside one clause instance. Detection is fully
 * deterministic (see lib/placeholders.ts) — no LLM call is needed to find `{{ variable }}` or
 * `[INSERT ...]` style gaps. Once fields are detected, `analyseClauseFields` asks the backend
 * which of them this contract already resolved (via `resolve_variables`'s alias table) so the
 * user never retypes something already known; "Ask assistant" then covers the rest via a
 * suggestion the user still reviews and can edit before applying.
 *
 * `knownValues` are locally-known values (e.g. from placeholders already visible elsewhere in
 * this clause) used to prefill fields before the backend lookup returns.
 */
export function ClauseFillDetailsModal({
  section,
  knownValues,
  contractId,
  onApply,
  onVariablesPersisted,
  onClose,
}: {
  section: ClauseSection;
  knownValues: Record<string, string>;
  contractId?: string;
  /** Called with the full replacement section markdown (heading + body) once Apply succeeds. */
  onApply: (nextSectionMarkdown: string) => void;
  /** Called with the full merged Variable Memory once newly-typed values persist. */
  onVariablesPersisted?: (variables: Record<string, string>) => void;
  onClose: () => void;
}) {
  const detected = useMemo(() => detectPlaceholders(section.markdown, knownValues), [section.markdown, knownValues]);
  const [fields, setFields] = useState<ClauseMissingField[]>(detected);
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(detected.map((f) => [f.key, f.currentValue ?? ""]))
  );
  const [applying, setApplying] = useState(false);
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);

  useEffect(() => {
    setFields(detected);
    setValues(Object.fromEntries(detected.map((f) => [f.key, f.currentValue ?? ""])));
    setAskError(null);
  }, [section.instanceId]);

  // Ask the backend which detected fields this contract already resolved. Only prefills —
  // never overwrites a value the user has already typed into the form.
  useEffect(() => {
    if (!contractId || detected.length === 0) return;
    let cancelled = false;
    analyseClauseFields(contractId, detected.map((f) => f.key))
      .then(({ known }) => {
        if (cancelled || Object.keys(known).length === 0) return;
        setFields((cur) =>
          cur.map((f) => (f.key in known && !f.currentValue ? { ...f, currentValue: known[f.key] } : f))
        );
        setValues((cur) => {
          const next = { ...cur };
          for (const [key, value] of Object.entries(known)) {
            if (!next[key]) next[key] = value;
          }
          return next;
        });
      })
      .catch(() => {
        /* best-effort prefill; the form still works from local detection alone */
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contractId, section.instanceId]);

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      e.stopPropagation();
      onClose();
    }
  }

  // All detected fields are required — a placeholder left in the form untouched is still a
  // placeholder left in the document, so Apply stays disabled until every field has a value.
  const allValid = fields.every((f) => (values[f.key] ?? "").trim() !== "");
  const stillMissing = fields.filter((f) => (values[f.key] ?? "").trim() === "");

  async function handleApply() {
    if (applying || !allValid) return;
    setApplying(true);
    const { markdown } = applyPlaceholderValues(section.markdown, fields, values);
    if (contractId && fields.length > 0) {
      try {
        const merged = await updateContractVariables(contractId, values);
        onVariablesPersisted?.(merged);
      } catch {
        /* best-effort persistence; the clause itself is still filled locally */
      }
    }
    onApply(markdown);
  }

  async function handleAskAssistant() {
    if (!contractId || asking || stillMissing.length === 0) return;
    setAsking(true);
    setAskError(null);
    try {
      const { suggestions, unresolved } = await fillClauseFields(
        contractId,
        section.markdown,
        stillMissing.map((f) => f.key),
      );
      setValues((cur) => ({ ...cur, ...suggestions }));
      if (unresolved.length > 0 && Object.keys(suggestions).length === 0) {
        setAskError("The assistant couldn't suggest values for these fields — they depend on facts only you know.");
      }
    } catch (err) {
      setAskError(err instanceof Error ? err.message : "Could not reach the assistant.");
    } finally {
      setAsking(false);
    }
  }

  return (
    <div className="playbook-overlay" role="dialog" aria-modal="true" aria-label="Fill clause details" onKeyDown={onKeyDown}>
      <div className="playbook-modal" style={{ maxWidth: 520 }}>
        <div className="playbook-modal-header">
          <div>
            <div className="text-sm font-semibold text-[color:var(--text)]">Fill details</div>
            <div className="text-xs text-[color:var(--text-muted)] mt-0.5">
              {section.title || "Untitled clause"} — {fields.length} missing field{fields.length === 1 ? "" : "s"}
            </div>
          </div>
          <button type="button" className="playbook-close" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="px-5 py-4 flex-1 overflow-auto" style={{ minHeight: fields.length ? undefined : 60 }}>
          {fields.length === 0 ? (
            <div className="text-sm text-[color:var(--text-muted)]">This clause has no missing details.</div>
          ) : (
            <div className="flex flex-col gap-3">
              {fields.map((field) => (
                <label key={field.key} className="flex flex-col gap-1 text-sm">
                  <span className="font-medium text-[color:var(--text)]">
                    {field.label}
                    {field.currentValue ? (
                      <span className="ml-2 text-xs font-normal text-[color:var(--text-muted)]">(known value)</span>
                    ) : null}
                  </span>
                  <input
                    type="text"
                    className="clause-input"
                    value={values[field.key] ?? ""}
                    placeholder={field.placeholder}
                    onChange={(e) => setValues((cur) => ({ ...cur, [field.key]: e.target.value }))}
                  />
                </label>
              ))}
            </div>
          )}

          {askError && <div className="text-xs text-[color:var(--danger,#b91c1c)] mt-2">{askError}</div>}

          <button
            type="button"
            className="playbook-btn-ghost mt-4"
            title={
              !contractId
                ? "Ask assistant is available once this clause belongs to a saved contract."
                : "Suggest values for the fields still left blank below."
            }
            disabled={!contractId || asking || stillMissing.length === 0}
            onClick={handleAskAssistant}
          >
            {asking ? "Asking…" : "Ask assistant"}
          </button>
        </div>

        <div className="playbook-modal-footer">
          <button type="button" className="playbook-btn-ghost" onClick={onClose} disabled={applying}>
            Cancel
          </button>
          {fields.length > 0 && (
            <button
              type="button"
              className="playbook-btn-primary"
              onClick={handleApply}
              disabled={applying || !allValid}
            >
              {applying ? "Applying…" : "Apply details"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}