"use client";

import { type FieldStatus, fieldStatus } from "@/lib/fieldStatus";

export type { FieldStatus };
export { fieldStatus };

const CONFIG: Record<FieldStatus, { label: string; icon: string; title: string; className: string }> = {
  known: {
    label: "Known value",
    icon: "\u2713",
    title: "This value was remembered from this contract.",
    className: "field-status-known",
  },
  new: {
    label: "New",
    icon: "\u25cf",
    title: "This value was entered during this fill operation.",
    className: "field-status-new",
  },
  required: {
    label: "Required",
    icon: "\u26a0",
    title: "This value is required before this clause can be completed.",
    className: "field-status-required",
  },
};

export function FieldStatusBadge({ status }: { status: FieldStatus }) {
  const cfg = CONFIG[status];
  return (
    <span className={`field-status-badge ${cfg.className}`} title={cfg.title}>
      <span aria-hidden="true">{cfg.icon}</span>
      {cfg.label}
    </span>
  );
}
