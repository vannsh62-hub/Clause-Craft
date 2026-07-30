"use client";

import { useEffect, useRef, useState } from "react";
import type { ClauseSection } from "@/lib/clauses";
import { suggestClauseEdit } from "@/lib/api";

const EXAMPLE_PROMPTS = [
  "Make this more favorable to the service provider",
  "Add a 30-day notice period",
  "Shorten this clause",
  "Make the language more formal",
];

/**
 * Edits a single clause *instance* in the current contract document — not the approved
 * clause library. Saving replaces only this clause's section in `markdown`/`editedMarkdown`
 * and leaves the rest of the document untouched; the caller is responsible for splicing the
 * result back in and re-running `renumberClauseHeadings` (see page.tsx `onSaveEditClause`).
 *
 * Deliberately has no knowledge of the clause library or `PUT /clauses/{id}` — this is an
 * edit of the instance only, per the "Edit clause" requirement.
 *
 * Combines two ways to edit:
 * - Manual: a plain textarea, "Apply Manual Changes" applies it directly.
 * - AI Assistant: a free-text instruction ("add a 30-day notice period"), "Suggest
 *   Changes" asks the backend to rewrite just this clause, shown side-by-side with the
 *   original for Approve/Reject before anything is applied.
 */
export function ClauseEditModal({
  contractId,
  section,
  onSave,
  onClose,
}: {
  contractId?: string;
  section: ClauseSection;
  /** Called with the full replacement section markdown (heading + body). Only fires once per
   *  open modal — the modal disables Save immediately on click to prevent a double save from a
   *  fast double-click or repeated Enter. */
  onSave: (nextSectionMarkdown: string) => void;
  onClose: () => void;
}) {
  const [text, setText] = useState(section.markdown);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // AI Assistant state.
  const [instruction, setInstruction] = useState("");
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [suggestion, setSuggestion] = useState<{ updated_clause: string; summary: string } | null>(null);

  // Reset to the (possibly new) section's text whenever a different clause is opened, but not
  // on every re-render of the same clause — that would clobber in-progress typing.
  useEffect(() => {
    setText(section.markdown);
    setError(null);
    setSaving(false);
    setInstruction("");
    setAiError(null);
    setSuggestion(null);
    setAiLoading(false);
  }, [section.instanceId]);

  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      e.stopPropagation();
      // Escape backs out of a suggestion preview first, then closes the modal.
      if (suggestion) {
        setSuggestion(null);
        return;
      }
      onClose();
    }
  }

  function validate(candidate: string): string | null {
    const trimmed = candidate.trim();
    if (!trimmed) {
      return "The clause can't be saved empty — remove it instead if that's the intent.";
    }
    if (!/^##[ \t]+\S/.test(trimmed)) {
      return 'The clause must keep its "## " heading — the first line should read "## Title" (optionally "## 1. Title").';
    }
    return null;
  }

  function handleManualSave() {
    if (saving) return; // guards against a double save from a fast double-click / repeated Enter
    const problem = validate(text);
    if (problem) {
      setError(problem);
      return;
    }
    setSaving(true);
    setError(null);
    // Preserve the original trailing spacing shape (the section as parsed always runs up to
    // the next heading or end-of-doc) so splicing back in doesn't collapse or duplicate blank
    // lines at the boundary.
    const normalized = text.endsWith("\n") ? text : text + "\n";
    onSave(normalized);
  }

  async function handleSuggestChanges() {
    if (aiLoading || !instruction.trim()) return;
    if (!contractId) {
      setAiError("No contract to ask the assistant about yet.");
      return;
    }
    setAiLoading(true);
    setAiError(null);
    setSuggestion(null);
    try {
      const result = await suggestClauseEdit(contractId, text, instruction.trim());
      setSuggestion(result);
    } catch (err) {
      setAiError(err instanceof Error ? err.message : "Could not get a suggestion.");
    } finally {
      setAiLoading(false);
    }
  }

  function handleApproveSuggestion() {
    if (!suggestion || saving) return;
    const problem = validate(suggestion.updated_clause);
    if (problem) {
      setAiError(problem);
      return;
    }
    setSaving(true);
    const normalized = suggestion.updated_clause.endsWith("\n")
      ? suggestion.updated_clause
      : suggestion.updated_clause + "\n";
    onSave(normalized);
  }

  function handleRejectSuggestion() {
    setSuggestion(null);
    setAiError(null);
  }

  return (
    <div
      className="clause-ai-edit-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Edit clause"
      onKeyDown={onKeyDown}
    >
      <div className="clause-ai-edit-modal">
        <div className="clause-ai-edit-header">
          <div>
            <div className="text-sm font-semibold text-[color:var(--text)]">Edit clause</div>
            <div className="text-xs text-[color:var(--text-muted)] mt-0.5">
              {section.title || "Untitled clause"} — editing this contract's copy only, not the clause library.
            </div>
          </div>
          <button type="button" className="clause-ai-edit-close" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="clause-ai-edit-body">
          {!suggestion ? (
            <>
              <textarea
                ref={textareaRef}
                className="clause-ai-edit-textarea"
                value={text}
                onChange={(e) => {
                  setText(e.target.value);
                  if (error) setError(null);
                }}
                spellCheck={false}
              />
              {error && <div className="clause-ai-edit-error">{error}</div>}

              <div className="clause-ai-edit-assistant">
                <div className="clause-ai-edit-assistant-label">AI Assistant</div>
                <textarea
                  className="clause-ai-edit-prompt"
                  placeholder='Describe the change you want, e.g. "add a 30-day notice period"'
                  value={instruction}
                  onChange={(e) => {
                    setInstruction(e.target.value);
                    if (aiError) setAiError(null);
                  }}
                  rows={2}
                />
                <div className="clause-ai-edit-chips">
                  {EXAMPLE_PROMPTS.map((p) => (
                    <button
                      key={p}
                      type="button"
                      className="clause-ai-edit-chip"
                      onClick={() => setInstruction(p)}
                    >
                      {p}
                    </button>
                  ))}
                </div>
                {aiError && <div className="clause-ai-edit-error">{aiError}</div>}
                <button
                  type="button"
                  className="clause-ai-edit-suggest-btn"
                  onClick={handleSuggestChanges}
                  disabled={aiLoading || !instruction.trim()}
                >
                  {aiLoading ? "Thinking…" : "Suggest Changes"}
                </button>
              </div>
            </>
          ) : (
            <div className="clause-ai-edit-preview">
              <div className="clause-ai-edit-preview-summary">
                {suggestion.summary || "Here's the suggested rewrite:"}
              </div>
              <div className="clause-ai-edit-preview-columns">
                <div className="clause-ai-edit-preview-col">
                  <div className="clause-ai-edit-preview-col-label">Original</div>
                  <pre className="clause-ai-edit-preview-text">{text}</pre>
                </div>
                <div className="clause-ai-edit-preview-col">
                  <div className="clause-ai-edit-preview-col-label">Suggested</div>
                  <pre className="clause-ai-edit-preview-text clause-ai-edit-preview-text-new">
                    {suggestion.updated_clause}
                  </pre>
                </div>
              </div>
            </div>
          )}
        </div>

        <div className="clause-ai-edit-footer">
          {!suggestion ? (
            <>
              <button type="button" className="playbook-btn-ghost" onClick={onClose} disabled={saving}>
                Cancel
              </button>
              <button
                type="button"
                className="playbook-btn-primary"
                onClick={handleManualSave}
                disabled={saving}
              >
                {saving ? "Saving…" : "Apply Manual Changes"}
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                className="playbook-btn-ghost"
                onClick={handleRejectSuggestion}
                disabled={saving}
              >
                Reject
              </button>
              <button
                type="button"
                className="playbook-btn-primary"
                onClick={handleApproveSuggestion}
                disabled={saving}
              >
                {saving ? "Applying…" : "Approve"}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
