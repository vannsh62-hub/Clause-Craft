import { parseFrame, splitFrames } from "./sse";
import type {
  Contract,
  ContractVersion,
  ExportInfo,
  RunAccepted,
  RunEvent,
  RunEventType,
  WorkspaceFile,
} from "./types";

const BASE = "/api/v1";

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail ?? `${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

export async function createContract(
  request: string,
  files: File[] = [],
  clauseIds: string[] = [],
  asTemplate = false,
): Promise<RunAccepted> {
  const body = new FormData();
  body.append("request", request);
  files.forEach((file) => body.append("files", file));
  if (clauseIds && clauseIds.length > 0) body.append("clause_ids", JSON.stringify(clauseIds));
  // Marks the first upload as a template to transform (Mode 2) rather than a reference.
  if (asTemplate) body.append("as_template", "true");
  return json(
    await fetch(`${BASE}/contracts`, {
      method: "POST",
      body,
    }),
  );
}

export interface ReplicateAccepted {
  contracts: RunAccepted[];
}

/**
 * Start several independent drafts from one uploaded document.
 *
 * Every copy is seeded with the same bytes, so they begin identical and differ only in what
 * each run drafts — the point being to compare variants, not to edit one document repeatedly.
 */
export async function replicateContract(
  request: string,
  file: File,
  copies: number,
): Promise<ReplicateAccepted> {
  const body = new FormData();
  body.append("request", request);
  body.append("files", file);
  body.append("copies", String(copies));
  return json(await fetch(`${BASE}/contracts/replicate`, { method: "POST", body }));
}

export async function answer(
  contractId: string,
  answers: Record<string, string>,
): Promise<RunAccepted> {
  return json(
    await fetch(`${BASE}/contracts/${contractId}/answers`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ answers }),
    }),
  );
}

export async function getContract(id: string): Promise<Contract> {
  return json(await fetch(`${BASE}/contracts/${id}`));
}

export interface ContractSummary {
  id: string;
  request: string;
  contract_type: string | null;
  status: string;
  created_at: string;
  finalized: boolean;
}

/** Recent contracts, newest first — the assistant history and the projects list. */
export async function listContracts(): Promise<ContractSummary[]> {
  return json(await fetch(`${BASE}/contracts`));
}

export async function getVersions(id: string): Promise<ContractVersion[]> {
  return json(await fetch(`${BASE}/contracts/${id}/versions`));
}

export async function getWorkspace(id: string): Promise<WorkspaceFile[]> {
  return json(await fetch(`${BASE}/contracts/${id}/workspace`));
}

export async function readWorkspaceFile(id: string, path: string): Promise<string> {
  const body = await json<{ content: string }>(
    await fetch(`${BASE}/contracts/${id}/workspace/${path}`),
  );
  return body.content;
}

export interface DocumentSaveResult {
  path: string;
  version_id: string;
  updated_at: string;
}

/**
 * Persist the Document Preview/Editing view's current markdown — manual edits and applied
 * assistant clause actions alike — so it survives a reload and is what `/export` regenerates
 * a docx from, instead of silently reverting to the drafting engine's original output.
 */
export async function saveDocument(id: string, markdown: string): Promise<DocumentSaveResult> {
  return json(
    await fetch(`${BASE}/contracts/${id}/document/save`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ markdown }),
    }),
  );
}

export async function createExport(id: string): Promise<ExportInfo> {
  return json(await fetch(`${BASE}/contracts/${id}/export`, { method: "POST" }));
}

export function downloadUrl(exportId: string): string {
  return `${BASE}/exports/${exportId}`;
}

export async function extractClauses(files: File[], prompt: string, contractId?: string) {
  const body = new FormData();
  files.forEach((f) => body.append("files", f));
  body.append("prompt", prompt);
  if (contractId) body.append("contract_id", contractId);
  return json(await fetch(`${BASE}/clauses/extract`, { method: "POST", body }));
}

export async function getClauseText(clauseId: string): Promise<{ title: string; body: string }> {
  return json(await fetch(`${BASE}/clauses/${encodeURIComponent(clauseId)}/text`));
}

export interface RenderedClauseForContract {
  id: string;
  version: number;
  title: string;
  text: string;
  /** Variables this contract never resolved — still shown as `{{ name }}` in `text`. */
  missing: string[];
}

/**
 * Render a clause using the values this contract already has — disclosing/receiving party,
 * dates, etc. No prompt to the user: whatever the drafting run collected is reused here.
 */
export async function renderClauseForContract(
  contractId: string,
  clauseId: string,
): Promise<RenderedClauseForContract> {
  return json(
    await fetch(
      `${BASE}/contracts/${contractId}/clauses/${encodeURIComponent(clauseId)}/render`,
      { method: "POST" },
    ),
  );
}

export interface ClauseSummary {
  id: string;
  title: string;
  contract_type: string;
  required: boolean;
  version: number;
  category: string;
  country: string;
  risk: string;
  status: string;
  body: string;
}

export interface ClauseWrite {
  id: string;
  title: string;
  contract_type: string;
  country: string;
  required: boolean;
  order: number;
  risk: string;
  body: string;
}

/** Browse the approved clause library, optionally filtered by contract type. */
export async function listClauses(contractType?: string): Promise<ClauseSummary[]> {
  const q = contractType ? `?contract_type=${encodeURIComponent(contractType)}` : "";
  return json(await fetch(`${BASE}/clauses${q}`));
}

/** Add a clause. Throws with the reason if the library loader rejects it. */
export async function createClause(clause: ClauseWrite): Promise<ClauseSummary> {
  return json(
    await fetch(`${BASE}/clauses`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(clause),
    }),
  );
}

/** Edit a clause (bumps its version). */
export async function updateClause(id: string, clause: ClauseWrite): Promise<ClauseSummary> {
  return json(
    await fetch(`${BASE}/clauses/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(clause),
    }),
  );
}

/** Remove a clause from the library. */
export async function deleteClause(id: string): Promise<void> {
  const res = await fetch(`${BASE}/clauses/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!res.ok && res.status !== 204) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail ?? `${res.status} ${res.statusText}`);
  }
}

export interface PlaybookView {
  yaml: string;
  rule_count: number;
}

/** The current playbook, as YAML. */
export async function getPlaybook(): Promise<PlaybookView> {
  return json(await fetch(`${BASE}/playbook`));
}

/** Validate and save an edited playbook. Throws with the reason if it is rejected. */
export async function savePlaybook(yamlText: string): Promise<PlaybookView> {
  return json(
    await fetch(`${BASE}/playbook`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ yaml: yamlText }),
    }),
  );
}

export interface RuleCondition {
  field: string;
  op: string;
  value?: unknown;
}

export interface PlaybookRule {
  id: string;
  when: RuleCondition[];
  kind: string; // require_section | forbid_section | set_value | flag
  target: string;
  value?: string | null;
  reason?: string;
  blocking?: boolean;
}

export interface RulesView {
  rules: PlaybookRule[];
  explanation: string;
}

/** The playbook rules as structured rows, for the table view. */
export async function getPlaybookRules(): Promise<RulesView> {
  return json(await fetch(`${BASE}/playbook/rules`));
}

export async function addPlaybookRule(rule: PlaybookRule): Promise<RulesView> {
  return json(
    await fetch(`${BASE}/playbook/rules`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(rule),
    }),
  );
}

export async function updatePlaybookRule(id: string, rule: PlaybookRule): Promise<RulesView> {
  return json(
    await fetch(`${BASE}/playbook/rules/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(rule),
    }),
  );
}

export async function deletePlaybookRule(id: string): Promise<RulesView> {
  return json(
    await fetch(`${BASE}/playbook/rules/${encodeURIComponent(id)}`, { method: "DELETE" }),
  );
}

export interface ClauseAnalyseResult {
  known: Record<string, string>;
  missing: string[];
}

/**
 * Split a clause instance's placeholder field names into what this contract already
 * resolved (via the backend's variable-alias table, same as `renderClauseForContract`)
 * versus what is genuinely missing. Read-only — never mutates the contract or the clause
 * library.
 */
export async function analyseClauseFields(
  contractId: string,
  fields: string[],
): Promise<ClauseAnalyseResult> {
  return json(
    await fetch(`${BASE}/contracts/${contractId}/clauses/analyse`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ fields }),
    }),
  );
}

export interface ClauseFillResult {
  suggestions: Record<string, string>;
  unresolved: string[];
}

/**
 * Persist newly-confirmed placeholder values into this contract's Variable Memory, so no
 * later clause — inserted, AI-generated, or manually edited — ever asks for the same value
 * twice. Returns the full resolved variable set after the merge; callers should replace their
 * local cache with it rather than patching it in, so it can't drift from what's stored.
 */
export async function updateContractVariables(
  contractId: string,
  values: Record<string, string>,
): Promise<Record<string, string>> {
  const { variables } = await json<{ variables: Record<string, string> }>(
    await fetch(`${BASE}/contracts/${contractId}/variables`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ values }),
    }),
  );
  return variables;
}

/**
 * Ask the assistant to suggest values for fields `analyseClauseFields` could not resolve.
 * Returns suggestions for the Fill-details modal's editable inputs — nothing is applied to
 * the document by this call.
 */
export async function fillClauseFields(
  contractId: string,
  clauseText: string,
  fields: string[],
): Promise<ClauseFillResult> {
  return json(
    await fetch(`${BASE}/contracts/${contractId}/clauses/fill`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ clause_text: clauseText, fields }),
    }),
  );
}

export interface ClauseEditSuggestion {
  updated_clause: string;
  summary: string;
}

/**
 * The Edit popover's ✨ AI Assistant action: ask the assistant to rewrite one clause per a
 * free-text instruction. Sends only that clause's own markdown — never the rest of the
 * document — and returns a suggestion for the popover's side-by-side preview. Nothing is
 * applied to the document by this call; the caller splices in the approved rewrite the
 * same way a manual edit is applied.
 */
export async function suggestClauseEdit(
  contractId: string,
  clauseMarkdown: string,
  instruction: string,
): Promise<ClauseEditSuggestion> {
  return json(
    await fetch(`${BASE}/contracts/${contractId}/clauses/edit/suggest`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ clause_markdown: clauseMarkdown, instruction }),
    }),
  );
}

export interface ClauseActionOut {
  action: "insert" | "replace" | "remove" | "fill" | string;
  clause_title: string;
  clause_id: string;
  after_clause_title: string;
  fields: Record<string, string>;
  reason: string;
}

export interface ClauseActionsResult {
  reply: string;
  actions: ClauseActionOut[];
}

/** One earlier turn of the same clause-assistant conversation — the client's own record,
 *  sent back each call since the server keeps no state between requests. */
export interface ClauseActionsHistoryTurn {
  message: string;
  reply: string;
}

/**
 * One turn of the assistant panel: propose clause edits from a chat message against the
 * document as it currently stands. Returns proposals only — nothing is applied here; the
 * caller applies each action the user accepts through the same client-side splice
 * functions the manual editor uses.
 *
 * `history` is this conversation's prior turns (oldest first), so a follow-up like
 * "add it" or "now do the next one" has something to resolve against instead of being
 * treated as a brand-new, context-free request each time.
 */
export async function proposeClauseActions(
  contractId: string,
  message: string,
  document: string,
  history: ClauseActionsHistoryTurn[] = [],
): Promise<ClauseActionsResult> {
  return json(
    await fetch(`${BASE}/contracts/${contractId}/clauses/assistant`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message, document, history }),
    }),
  );
}

/**
 * Read a run's event stream, resuming from `fromSeq`.
 *
 * Deliberately not `EventSource`. `EventSource` reconnects on its own, and it would
 * reconnect at `?seq=0` — replaying the whole run and duplicating everything the client
 * already rendered. Here we track the last `seq` we saw and reconnect from exactly there,
 * which is what the server's replay endpoint is for.
 *
 * Stops on a terminal event. The server closes the stream then too, but a client that
 * decides for itself does not depend on that.
 */
export async function streamRun(
  runId: string,
  fromSeq: number,
  onEvent: (event: RunEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const TERMINAL: RunEventType[] = ["complete", "input_required", "error"];
  let lastSeq = fromSeq;

  while (!signal.aborted) {
    try {
      const response = await fetch(`${BASE}/runs/${runId}/events?seq=${lastSeq}`, { signal });
      if (!response.ok || !response.body) throw new Error(`stream failed: ${response.status}`);

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const { frames, rest } = splitFrames(buffer);
        buffer = rest;

        for (const frame of frames) {
          const event = parseFrame(frame);
          if (!event || event.seq <= lastSeq) continue; // never replay what we rendered
          lastSeq = event.seq;
          onEvent(event);
          if (TERMINAL.includes(event.event)) return;
        }
      }
    } catch (error) {
      if (signal.aborted) return;
      // A dropped connection is expected. Reconnect from the last seq we actually saw;
      // the server replays the gap from Postgres.
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }
}