"use client";

import { useEffect, useRef } from "react";
import type { ClauseSection } from "@/lib/clauses";

/** Confirms removal of one clause instance before it's spliced out. Shows the clause title so
 *  the user isn't confirming blind, and disables both buttons once a removal is in flight so a
 *  fast double-click can't fire two removals. */
export function ClauseRemoveConfirm({
  section,
  removing,
  onConfirm,
  onCancel,
}: {
  section: ClauseSection;
  removing: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    confirmRef.current?.focus();
  }, []);

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      e.stopPropagation();
      onCancel();
    }
  }

  return (
    <div className="playbook-overlay" role="dialog" aria-modal="true" aria-label="Remove clause" onKeyDown={onKeyDown}>
      <div className="playbook-modal" style={{ maxWidth: 440 }}>
        <div className="playbook-modal-header">
          <div className="text-sm font-semibold text-[color:var(--text)]">Remove clause?</div>
          <button type="button" className="playbook-close" aria-label="Close" onClick={onCancel}>
            ×
          </button>
        </div>
        <div className="px-5 py-4 text-sm text-[color:var(--text)]">
          Remove <b>&ldquo;{section.title || "Untitled clause"}&rdquo;</b> from this contract? The
          remaining clauses will be renumbered. This won&apos;t affect the clause library.
        </div>
        <div className="playbook-modal-footer">
          <button type="button" className="playbook-btn-ghost" onClick={onCancel} disabled={removing}>
            Cancel
          </button>
          <button
            type="button"
            className="playbook-btn-primary"
            style={{ background: "#e11d48" }}
            onClick={onConfirm}
            disabled={removing}
          >
            {removing ? "Removing…" : "Remove clause"}
          </button>
        </div>
      </div>
    </div>
  );
}