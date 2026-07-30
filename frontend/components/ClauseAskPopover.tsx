"use client";

import { useEffect, useRef, useState } from "react";
import { proposeClauseActions, type ClauseActionOut } from "@/lib/api";
import type { ClauseSection } from "@/lib/clauses";

interface ChatTurn {
  message: string;
  reply: string;
  actions: ClauseActionOut[];
  resolved: Set<number>;
}

const ACTION_VERBS: Record<string, string> = {
  insert: "Insert",
  replace: "Edit",
  remove: "Remove",
  fill: "Fill in",
};

function describeAction(a: ClauseActionOut): string {
  switch (a.action) {
    case "insert":
      return a.after_clause_title ? `Insert "${a.clause_id}" after this clause` : `Insert "${a.clause_id}"`;
    case "replace":
      return `Rewrite this clause using "${a.clause_id}"`;
    case "remove":
      return `Remove this clause`;
    case "fill":
      return `Fill ${Object.keys(a.fields).length} field(s) in this clause`;
    default:
      return a.reason || a.action;
  }
}

/** True if the proposed action targets the clause the popover is anchored to, rather than
 *  some other clause in the document. Replace/remove/fill are scoped by clause_title;
 *  insert is scoped by after_clause_title (it must be inserted right after this clause). */
function targetsThisClause(a: ClauseActionOut, section: ClauseSection): boolean {
  const norm = (s: string) => s.trim().toLowerCase();
  if (a.action === "insert") return norm(a.after_clause_title || "") === norm(section.title);
  return norm(a.clause_title || "") === norm(section.title);
}

/**
 * A small pop-out anchored to a single clause block (Colab-style block actions), opened
 * from the clause's hover toolbar via an "Ask" trigger. Scoped to one clause: proposed
 * actions that would touch a different clause are never one-click-applicable here — they
 * render with a "this affects another clause" notice and require an explicit confirm.
 */
export function ClauseAskPopover({
  contractId,
  section,
  getDocument,
  onApplyAction,
  onClose,
}: {
  contractId?: string;
  section: ClauseSection;
  getDocument: () => string;
  onApplyAction: (action: ClauseActionOut) => Promise<void> | void;
  onClose: () => void;
}) {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [applyingKey, setApplyingKey] = useState<string | null>(null);
  const [confirmingKey, setConfirmingKey] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) onClose();
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  async function send() {
    const message = input.trim();
    if (!message || sending || !contractId) return;
    setSending(true);
    setError(null);
    try {
      // Scope the free-text instruction to this clause only, so the backend proposes
      // actions against it specifically rather than the whole contract.
      const scoped = `Regarding only the clause "${section.title}": ${message}`;
      const history = turns.map((t) => ({ message: t.message, reply: t.reply }));
      const { reply, actions } = await proposeClauseActions(contractId, scoped, getDocument(), history);
      setTurns((cur) => [...cur, { message, reply, actions, resolved: new Set() }]);
      setInput("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reach the assistant.");
    } finally {
      setSending(false);
    }
  }

  async function apply(turnIndex: number, actionIndex: number) {
    const key = `${turnIndex}:${actionIndex}`;
    if (applyingKey) return;
    const turn = turns[turnIndex];
    if (!turn || turn.resolved.has(actionIndex)) return;
    const action = turn.actions[actionIndex];

    // Never let a same-clause popover silently touch another clause — require one extra
    // explicit confirm click first.
    if (!targetsThisClause(action, section) && confirmingKey !== key) {
      setConfirmingKey(key);
      return;
    }

    setApplyingKey(key);
    setConfirmingKey(null);
    setError(null);
    try {
      await onApplyAction(action);
      setTurns((cur) =>
        cur.map((t, i) => (i === turnIndex ? { ...t, resolved: new Set(t.resolved).add(actionIndex) } : t))
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not apply that change.");
    } finally {
      setApplyingKey(null);
    }
  }

  function dismiss(turnIndex: number, actionIndex: number) {
    setTurns((cur) =>
      cur.map((t, i) => (i === turnIndex ? { ...t, resolved: new Set(t.resolved).add(actionIndex) } : t))
    );
    setConfirmingKey(null);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" && !sending) {
      e.preventDefault();
      void send();
    }
  }

  return (
    <div
      ref={rootRef}
      role="dialog"
      aria-label={`Ask assistant about ${section.title || "this clause"}`}
      className="clause-ask-popover absolute right-3 top-full mt-2 z-20 w-[340px] max-w-[90vw] rounded-2xl border border-[color:var(--border)] bg-[color:var(--surface-strong,#fff)] shadow-xl flex flex-col"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-center justify-between px-3 py-2 border-b border-[color:var(--border,rgba(0,0,0,0.08))]">
        <div className="text-xs font-semibold text-[color:var(--text)] truncate pr-2">
          {section.title || "Clause"}
        </div>
        <button type="button" className="playbook-close" aria-label="Close" onClick={onClose}>
          ×
        </button>
      </div>

      <div className="max-h-72 overflow-auto px-3 py-2 flex flex-col gap-3">
        {turns.length === 0 && (
          <div className="text-xs text-[color:var(--text-muted)]">How can I help with this clause?</div>
        )}
        {turns.map((turn, ti) => (
          <div key={ti} className="flex flex-col gap-1.5">
            <div className="text-xs text-[color:var(--text)] self-end max-w-[90%] bg-[rgba(15,118,110,0.1)] rounded-2xl px-2.5 py-1.5">
              {turn.message}
            </div>
            <div className="text-xs text-[color:var(--text)] max-w-[95%]">{turn.reply}</div>
            {turn.actions.map((a, ai) => {
              const key = `${ti}:${ai}`;
              const other = !targetsThisClause(a, section);
              return (
                <div
                  key={ai}
                  className="text-[11px] border border-[color:var(--border,rgba(0,0,0,0.1))] rounded-xl px-2.5 py-2 flex flex-col gap-1.5"
                >
                  <span>
                    <span className="font-medium">{ACTION_VERBS[a.action] ?? a.action}</span>: {describeAction(a)}
                  </span>
                  {other && !turn.resolved.has(ai) && (
                    <span className="text-amber-700">
                      This would affect a different clause ({a.clause_title || a.after_clause_title}).
                    </span>
                  )}
                  {turn.resolved.has(ai) ? (
                    <span className="text-[color:var(--text-muted)]">Done</span>
                  ) : (
                    <span className="flex gap-2 shrink-0 self-end">
                      <button
                        type="button"
                        className="playbook-btn-ghost"
                        onClick={() => dismiss(ti, ai)}
                        disabled={applyingKey === key}
                      >
                        Dismiss
                      </button>
                      <button
                        type="button"
                        className="playbook-btn-primary"
                        onClick={() => apply(ti, ai)}
                        disabled={applyingKey !== null}
                      >
                        {applyingKey === key
                          ? "Applying…"
                          : other && confirmingKey !== key
                          ? "Confirm target?"
                          : other
                          ? "Yes, apply anyway"
                          : "Apply"}
                      </button>
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        ))}
        {error && <div className="text-[11px] text-[color:var(--danger,#b91c1c)]">{error}</div>}
      </div>

      <div className="px-3 py-2 border-t border-[color:var(--border,rgba(0,0,0,0.08))] flex gap-2">
        <input
          ref={inputRef}
          type="text"
          className="clause-input flex-1 text-xs"
          placeholder="How can I help with this clause?"
          value={input}
          disabled={!contractId || sending}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <button
          type="button"
          className="playbook-btn-primary text-xs"
          onClick={() => void send()}
          disabled={!contractId || sending || !input.trim()}
        >
          {sending ? "…" : "Send"}
        </button>
      </div>
    </div>
  );
}