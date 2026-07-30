import type { Todo } from "@/lib/types";

/**
 * The agent's live plan. This is the deep-agent property made visible: the list is written by
 * the agent before it starts, and rewritten whenever the facts change.
 */
const MARK: Record<Todo["status"], string> = {
  pending: "○",
  in_progress: "◐",
  done: "✔",
  cancelled: "×",
};

const TONE: Record<Todo["status"], string> = {
  pending: "status-pending",
  in_progress: "status-active",
  done: "status-done",
  cancelled: "status-cancelled",
};

export function Plan({ todos }: { todos: Todo[] }) {
  if (todos.length === 0) return null;

  const done = todos.filter((t) => t.status === "done").length;

  return (
    <section className="work-card">
      <h2 className="mb-4 flex items-baseline justify-between card-title">
        <span>Drafting plan</span>
        <span className="text-xs font-normal muted-copy">
          {done} of {todos.length}
        </span>
      </h2>
      <ol className="space-y-3">
        {todos.map((todo, i) => (
          <li
            key={i}
            className={`plan-step flex items-start gap-3 text-sm text-[color:var(--text)] ${todo.status === "done" ? "plan-step-done" : todo.status === "in_progress" ? "plan-step-active" : todo.status === "cancelled" ? "status-cancelled" : ""}`}
          >
            <span className={`plan-mark ${TONE[todo.status]}`}>{MARK[todo.status]}</span>
            <div className="flex-1">
              <div className={todo.status === "done" ? "plan-done-text" : todo.status === "cancelled" ? "status-cancelled" : ""}>
                {todo.task}
              </div>
              {todo.status === "in_progress" ? <span className="plan-working-badge">Working</span> : null}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
