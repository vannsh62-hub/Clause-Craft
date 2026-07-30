"use client";

import { useEffect, useState } from "react";
import type { TraceEntry } from "@/lib/types";

interface TraceItem {
  id: string;
  title: string;
  subtitle?: string;
  completed?: boolean;
  pending?: boolean;
}

const BUSY_MESSAGES = [
  "Thinking",
  "Rephrasing",
  "Reviewing clauses",
  "Refining language",
];

function formatEntry(entry: TraceEntry): TraceItem {
  const title = entry.output
    ? entry.output.split("\n")[0].slice(0, 86)
    : `${entry.tool}…`;

  return {
    id: `${entry.seq}-${entry.tool}`,
    title,
    subtitle: entry.agent !== "orchestrator" ? `via ${entry.agent}` : undefined,
    completed: entry.output !== undefined,
    pending: entry.output === undefined,
  };
}

/** What the agent actually did, in order. A tool call is pending until its result arrives. */
export function Trace({ entries, busy }: { entries: TraceEntry[]; busy: boolean }) {
  const [collapsed, setCollapsed] = useState(false);
  const [statusIndex, setStatusIndex] = useState(0);
  const [dotIndex, setDotIndex] = useState(0);

  useEffect(() => {
    if (!busy) return undefined;
    const timer = window.setInterval(() => {
      setStatusIndex((current) => (current + 1) % BUSY_MESSAGES.length);
    }, 2800);
    return () => window.clearInterval(timer);
  }, [busy]);

  useEffect(() => {
    if (!busy) {
      setDotIndex(0);
      return undefined;
    }

    const dotTimer = window.setInterval(() => {
      setDotIndex((current) => (current + 1) % 3);
    }, 500);
    return () => window.clearInterval(dotTimer);
  }, [busy]);

  if (!busy && entries.length === 0) return null;

  const activeStatus = `${BUSY_MESSAGES[statusIndex]}${".".repeat(dotIndex + 1)}`;
  const timeline: TraceItem[] = [];

  if (busy) {
    timeline.push({
      id: "work-hint",
      title: activeStatus,
      subtitle: "Live draft refinement",
      pending: true,
    });
  }

  timeline.push(...entries.map(formatEntry));

  return (
    <section className={`work-card trace-card ${collapsed ? "trace-card-collapsed" : ""}`}>
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <p className="eyebrow">{busy ? "Working …" : "Activity"}</p>
          <p className="muted-copy">{busy ? activeStatus : "Agent activity timeline"}</p>
        </div>
        <button
          type="button"
          onClick={() => setCollapsed((current) => !current)}
          className="trace-header-actions"
          aria-expanded={!collapsed}
        >
          <span className="trace-spinner" aria-hidden="true" />
          <span className="trace-collapse-label">{collapsed ? "Expand" : "Collapse"}</span>
          <span className={`trace-collapse-icon ${collapsed ? "trace-collapse-icon-rotated" : ""}`} aria-hidden="true">▾</span>
        </button>
      </div>
      <ul className="trace-list">
        {timeline.map((item) => (
          <li
            key={item.id}
            className={`trace-step ${item.pending ? "trace-step-pending" : ""} ${item.id === "work-hint" ? "trace-step-active" : ""}`}
          >
            <div className={`trace-dot ${item.completed ? "trace-dot-done" : item.id === "work-hint" ? "trace-dot-active" : item.pending ? "trace-dot-pending" : ""}`} />
            <div className="trace-step-content">
              <p className="trace-step-title">{item.title}</p>
              {item.subtitle ? <p className="trace-step-meta">{item.subtitle}</p> : null}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
