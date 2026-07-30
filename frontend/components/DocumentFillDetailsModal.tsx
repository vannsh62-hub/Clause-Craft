"use client";

import { useEffect, useMemo, useState } from "react";
import {
  parseClauseSections,
  replaceClauseSection,
  renumberClauseHeadings,
  type ClauseSection,
} from "@/lib/clauses";
import { detectPlaceholders, applyPlaceholderValues, type ClauseMissingField } from "@/lib/placeholders";
import { analyseClauseFields, fillClauseFields, updateContractVariables } from "@/lib/api";

interface ClauseFieldGroup {
  section: ClauseSection;
  fields: ClauseMissingField[];
}

/** Every field across the whole document, keyed the same way the per-clause modal keys
 *  them within one clause — but here a key is only unique *within* its clause, so the
 *  input map is keyed by `${instanceId}:${field.key}` to avoid two different clauses'
 *  same-named field (e.g. two "{{ effective_date }}" in different clauses) colliding. */
function fieldInputKey(instanceId: string, fieldKey: string): string {
  return `${instanceId}:${fieldKey}`;
}

/**
 * Document-wide counterpart to `ClauseFillDetailsModal`: scans every clause in the
 * document for unresolved placeholders (deterministically, via lib/placeholders.ts — no
 * LLM call needed to find them) and asks for every missing value in one pass, grouped by
 * clause, instead of opening the per-clause modal one clause at a time.
 *
 * Applying replaces each affected clause's markdown in the document — via the same
 * `replaceClauseSection` splice function the manual editor and clause-anchored assistant
 * use — so clauses with no missing fields are left byte-for-byte untouched.
 */
export function DocumentFillDetailsModal({
  document,
  knownValues,
  contractId,
  onApply,
  onVariablesPersisted,
  onClose,
}: {
  document: string;
  knownValues: Record<string, string>;
  contractId?: string;
  /** Called with the full updated document markdown once Apply succeeds. */
  onApply: (nextDocument: string) => void;
  /** Called with the full merged Variable Memory once newly-typed values persist. */
  onVariablesPersisted?: (variables: Record<string, string>) => void;
  onClose: () => void;
}) {
  const groups = useMemo<ClauseFieldGroup[]>(() => {
    return parseClauseSections(document)
      .map((section) => ({ section, fields: detectPlaceholders(section.markdown, knownValues) }))
      .filter((g) => g.fields.length > 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [document]);

  const allFieldKeys = useMemo(
    () => groups.flatMap((g) => g.fields.map((f) => f.key)),
    [groups]
  );

  const [values, setValues] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {};
    for (const g of groups) {
      for (const f of g.fields) init[fieldInputKey(g.section.instanceId, f.key)] = f.currentValue ?? "";
    }
    return init;
  });
  const [applying, setApplying] = useState(false);
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);

  // Prefill from the contract's already-resolved variables, same lookup the per-clause
  // modal uses — never overwrites a value the user has already typed.
  useEffect(() => {
    if (!contractId || allFieldKeys.length === 0) return;
    let cancelled = false;
    analyseClauseFields(contractId, Array.from(new Set(allFieldKeys)))
      .then(({ known }) => {
        if (cancelled || Object.keys(known).length === 0) return;
        setValues((cur) => {
          const next = { ...cur };
          for (const g of groups) {
            for (const f of g.fields) {
              const inputKey = fieldInputKey(g.section.instanceId, f.key);
              if (f.key in known && !next[inputKey]) next[inputKey] = known[f.key];
            }
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
  }, [contractId]);

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      e.stopPropagation();
      onClose();
    }
  }

  const totalFields = groups.reduce((n, g) => n + g.fields.length, 0);
  const stillMissingCount = groups.reduce(
    (n, g) => n + g.fields.filter((f) => (values[fieldInputKey(g.section.instanceId, f.key)] ?? "").trim() === "").length,
    0
  );
  const allValid = stillMissingCount === 0;

  async function handleApply() {
    if (applying || !allValid || groups.length === 0) return;
    setApplying(true);
    let next = document;
    // Apply clause-by-clause via the same splice function the manual editor uses, so any
    // clause with no missing fields is left untouched and offsets stay correct as each
    // replacement shifts the document.
    const canonicalValues: Record<string, string> = {};
    for (const g of groups) {
      const fieldValues: Record<string, string> = {};
      for (const f of g.fields) {
        const value = values[fieldInputKey(g.section.instanceId, f.key)] ?? "";
        fieldValues[f.key] = value;
        if (value.trim() !== "") canonicalValues[f.key] = value;
      }
      const { markdown } = applyPlaceholderValues(g.section.markdown, g.fields, fieldValues);
      next = replaceClauseSection(next, g.section.instanceId, markdown);
    }
    if (contractId && Object.keys(canonicalValues).length > 0) {
      try {
        const merged = await updateContractVariables(contractId, canonicalValues);
        onVariablesPersisted?.(merged);
      } catch {
        /* best-effort persistence; the document itself is still filled locally */
      }
    }
    onApply(renumberClauseHeadings(next));
  }

  async function handleAskAssistant() {
    if (!contractId || asking) return;
    const groupsWithGaps = groups
      .map((g) => ({
        g,
        missing: g.fields.filter((f) => (values[fieldInputKey(g.section.instanceId, f.key)] ?? "").trim() === ""),
      }))
      .filter((x) => x.missing.length > 0);
    if (groupsWithGaps.length === 0) return;
    setAsking(true);
    setAskError(null);
    try {
      const results = await Promise.all(
        groupsWithGaps.map(({ g, missing }) =>
          fillClauseFields(
            contractId,
            g.section.markdown,
            missing.map((f) => f.key)
          ).then((r) => ({ section: g.section, ...r }))
        )
      );
      setValues((cur) => {
        const next = { ...cur };
        for (const r of results) {
          for (const [key, value] of Object.entries(r.suggestions)) {
            next[fieldInputKey(r.section.instanceId, key)] = value;
          }
        }
        return next;
      });
      const gotNothing = results.every((r) => Object.keys(r.suggestions).length === 0);
      if (gotNothing) {
        setAskError("The assistant couldn't suggest values for these fields — they depend on facts only you know.");
      }
    } catch (err) {
      setAskError(err instanceof Error ? err.message : "Could not reach the assistant.");
    } finally {
      setAsking(false);
    }
  }

  return (
    <div className="playbook-overlay" role="dialog" aria-modal="true" aria-label="Fill all missing details" onKeyDown={onKeyDown}>
      <div className="playbook-modal" style={{ maxWidth: 620 }}>
        <div className="playbook-modal-header">
          <div>
            <div className="text-sm font-semibold text-[color:var(--text)]">Fill all missing details</div>
            <div className="text-xs text-[color:var(--text-muted)] mt-0.5">
              {totalFields === 0
                ? "No missing details found in this document."
                : `${totalFields} missing field${totalFields === 1 ? "" : "s"} across ${groups.length} clause${groups.length === 1 ? "" : "s"}`}
            </div>
          </div>
          <button type="button" className="playbook-close" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="px-5 py-4 flex-1 overflow-auto" style={{ minHeight: groups.length ? undefined : 60 }}>
          {groups.length === 0 ? (
            <div className="text-sm text-[color:var(--text-muted)]">
              Every placeholder in this document already has a value.
            </div>
          ) : (
            <div className="flex flex-col gap-5">
              {groups.map((g) => (
                <div key={g.section.instanceId} className="flex flex-col gap-3">
                  <div className="text-xs font-semibold uppercase tracking-wide text-[color:var(--text-muted)]">
                    {g.section.title || "Untitled clause"}
                  </div>
                  {g.fields.map((field) => {
                    const inputKey = fieldInputKey(g.section.instanceId, field.key);
                    return (
                      <label key={inputKey} className="flex flex-col gap-1 text-sm">
                        <span className="font-medium text-[color:var(--text)]">
                          {field.label}
                          {field.currentValue ? (
                            <span className="ml-2 text-xs font-normal text-[color:var(--text-muted)]">
                              (known value)
                            </span>
                          ) : null}
                        </span>
                        <input
                          type="text"
                          className="clause-input"
                          value={values[inputKey] ?? ""}
                          placeholder={field.placeholder}
                          onChange={(e) => setValues((cur) => ({ ...cur, [inputKey]: e.target.value }))}
                        />
                      </label>
                    );
                  })}
                </div>
              ))}
            </div>
          )}

          {askError && <div className="text-xs text-[color:var(--danger,#b91c1c)] mt-2">{askError}</div>}

          {groups.length > 0 && (
            <button
              type="button"
              className="playbook-btn-ghost mt-4"
              title={
                !contractId
                  ? "Ask assistant is available once this document belongs to a saved contract."
                  : "Suggest values for every field still left blank above."
              }
              disabled={!contractId || asking || stillMissingCount === 0}
              onClick={handleAskAssistant}
            >
              {asking ? "Asking…" : "Ask assistant"}
            </button>
          )}
        </div>

        <div className="playbook-modal-footer">
          <button type="button" className="playbook-btn-ghost" onClick={onClose} disabled={applying}>
            Cancel
          </button>
          {groups.length > 0 && (
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