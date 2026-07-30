export type TodoStatus = "pending" | "in_progress" | "done" | "cancelled";

export interface Todo {
  task: string;
  status: TodoStatus;
}

export interface Question {
  name: string;
  question: string;
  type: "date" | "text" | "duration" | "money" | "enum";
}

export type RunEventType =
  | "stage"
  | "todo_update"
  | "tool_call"
  | "tool_result"
  | "input_required"
  | "complete"
  | "error";

export interface RunEvent {
  seq: number;
  event: RunEventType;
  data: Record<string, unknown>;
}

export interface RunAccepted {
  contract_id: string;
  run_id: string;
  events: string;
}

export interface ContractVersion {
  id: string;
  attempt: number;
  score: number | null;
  passed: boolean | null;
  needs_human_review: boolean | null;
  finalized_at: string | null;
  clause_ids: string[];
}

export interface Contract {
  id: string;
  contract_type: string | null;
  jurisdiction: string;
  status: string;
  request: string;
  created_at: string;
  //  Every deal-term/party value resolved for this contract so far — the Contract Variable
  //  Memory that powers Fill-details/Insert/AI-edit auto-fill.
  variables: Record<string, string>;
}

export interface ExportInfo {
  id: string;
  format: string;
  sha256: string;
  size_bytes: number;
}

export interface WorkspaceFile {
  path: string;
  read_only: boolean;
  version: number;
  size: number;
  updated_at: string;
}

/** A tool call and, once it returns, its result. */
export interface TraceEntry {
  seq: number;
  tool: string;
  agent: string;
  output?: string;
  truncated?: boolean;
}