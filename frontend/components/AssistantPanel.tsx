"use client";

import { useState } from "react";
import { proposeClauseActions, type ClauseActionOut } from "@/lib/api";

interface ChatTurn {
  message: string;
  reply: string;
  actions: ClauseActionOut[];
  /** Indices into `actions` the user has already applied or dismissed, so a re-render
   *  (or a slow second click) can't apply the same proposal twice. */
  resolved: Set<number>;
}

const ACTION_VERBS: Record<string, string> = {
  insert: "Insert",
  replace: "Replace",
  remove: "Remove",
  fill: "Fill in",
};

function describeAction(a: ClauseActionOut): string {
  switch (a.action) {
    case "insert":
      return a.after_clause_title
        ? `Insert "${a.clause_id}" after "${a.after_clause_title}"`
        : `Insert "${a.clause_id}" at the end`;
    case "replace":
      return `Replace "${a.clause_title}" with "${a.clause_id}"`;
    case "remove":
      return `Remove "${a.clause_title}"`;
    case "fill":
      return `Fill ${Object.keys(a.fields).length} field(s) in "${a.clause_title}"`;
    default:
      return a.reason || a.action;
  }
}

/**
 * A chat panel alongside the document editor. The assistant never authors clause text —
 * it only proposes structured actions (insert an approved library clause, replace/remove
 * an existing one, fill in placeholder fields), which the user applies one at a time via
 * `onApplyAction`. Applying is the parent's job: it owns the document and the same
 * deterministic splice functions the manual context menu uses, so an assistant-proposed
 * edit is applied through exactly the same path a manual one would be.
 */
export function AssistantPanel({
  contractId,
  getDocument,
  onApplyAction,
  onClose,
}: {
  contractId?: string;
  /** Called lazily so the panel always sends the document as it stands right now,
   *  including edits made since the panel opened. */
  getDocument: () => string;
  /** Apply one accepted action to the document. May reject (e.g. a stale/ambiguous
   *  target) — the panel surfaces that as an inline error rather than silently dropping
   *  the proposal. */
  onApplyAction: (action: ClauseActionOut) => Promise<void> | void;
  onClose: () => void;
}) {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [applyingKey, setApplyingKey] = useState<string | null>(null);

  async function send() {
    const message = input.trim();
    if (!message || sending || !contractId) return;
    setSending(true);
    setError(null);
    try {
      const history = turns.map((t) => ({ message: t.message, reply: t.reply }));
      const { reply, actions } = await proposeClauseActions(contractId, message, getDocument(), history);
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
    setApplyingKey(key);
    setError(null);
    try {
      await onApplyAction(turn.actions[actionIndex]);
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
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" && !sending) {
      e.preventDefault();
      void send();
    }
  }

  return (
    <div className="assistant-panel flex flex-col h-full" role="complementary" aria-label="Assistant">
      <div className="flex items-center justify-between px-4 py-3 border-b border-[color:var(--border,rgba(0,0,0,0.08))]">
        <div className="text-sm font-semibold text-[color:var(--text)]">Assistant</div>
        <button type="button" className="playbook-close" aria-label="Close" onClick={onClose}>
          ×
        </button>
      </div>

      <div className="flex-1 min-h-0 overflow-auto px-4 py-3 flex flex-col gap-4">
        {turns.length === 0 && (
          <div className="text-sm text-[color:var(--text-muted)]">
            Ask for a clause change — e.g. &ldquo;add a governing law clause&rdquo; or &ldquo;remove the
            indemnity clause&rdquo;.
          </div>
        )}
        {turns.map((turn, ti) => (
          <div key={ti} className="flex flex-col gap-2">
            <div className="text-sm text-[color:var(--text)] self-end max-w-[85%] bg-[rgba(15,118,110,0.1)] rounded-2xl px-3 py-2">
              {turn.message}
            </div>
            <div className="text-sm text-[color:var(--text)] max-w-[90%]">{turn.reply}</div>
            {turn.actions.map((a, ai) => (
              <div
                key={ai}
                className="text-xs border border-[color:var(--border,rgba(0,0,0,0.1))] rounded-xl px-3 py-2 flex items-center justify-between gap-2"
              >
                <span>
                  <span className="font-medium">{ACTION_VERBS[a.action] ?? a.action}</span>: {describeAction(a)}
                </span>
                {turn.resolved.has(ai) ? (
                  <span className="text-[color:var(--text-muted)]">Done</span>
                ) : (
                  <span className="flex gap-2 shrink-0">
                    <button
                      type="button"
                      className="playbook-btn-ghost"
                      onClick={() => dismiss(ti, ai)}
                      disabled={applyingKey === `${ti}:${ai}`}
                    >
                      Dismiss
                    </button>
                    <button
                      type="button"
                      className="playbook-btn-primary"
                      onClick={() => apply(ti, ai)}
                      disabled={applyingKey !== null}
                    >
                      {applyingKey === `${ti}:${ai}` ? "Applying…" : "Apply"}
                    </button>
                  </span>
                )}
              </div>
            ))}
          </div>
        ))}
        {error && <div className="text-xs text-[color:var(--danger,#b91c1c)]">{error}</div>}
      </div>

      <div className="px-4 py-3 border-t border-[color:var(--border,rgba(0,0,0,0.08))] flex gap-2">
        <input
          type="text"
          className="clause-input flex-1"
          placeholder={contractId ? "Ask for a clause change…" : "Available once the contract is saved."}
          value={input}
          disabled={!contractId || sending}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <button
          type="button"
          className="playbook-btn-primary"
          onClick={() => void send()}
          disabled={!contractId || sending || !input.trim()}
        >
          {sending ? "Asking…" : "Send"}
        </button>
      </div>
    </div>
  );
}