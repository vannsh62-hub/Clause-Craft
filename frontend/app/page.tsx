"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Questions } from "@/components/Questions";
import { ClauseInserter } from "@/components/ClauseInserter";
import { ClauseEditModal } from "@/components/Clauseeditmodal";
import { ClauseRemoveConfirm } from "@/components/ClauseRemoveConfirm";
import { ClauseFillDetailsModal } from "@/components/Clausefilldetailsmodal";
import { DocumentFillDetailsModal } from "@/components/DocumentFillDetailsModal";
import {
  ContractOutlineSidebar,
  ContractIntelligenceSidebar,
  ClauseSuggestionCards,
  ClauseAnalyticsFooter,
  ClauseExplainPopover,
  openClauseExplain,
  riskColor,
} from "@/components/ContractIntelligencePanel";
import { analyzeContract, type ContractAnalysis, type NegotiationPerspective } from "@/lib/api";
import {
  clauseSectionAt as sharedClauseSectionAt,
  findClauseByTitle,
  insertClauseSection,
  parseClauseSections,
  removeClauseSection as sharedRemoveClauseSection,
  replaceClauseSection as sharedReplaceClauseSection,
  renumberClauseHeadings as sharedRenumberClauseHeadings,
  type ClauseSection,
} from "@/lib/clauses";
import { detectPlaceholders, applyPlaceholderValues, resolveKnownPlaceholders } from "@/lib/placeholders";
import { AssistantPanel } from "@/components/AssistantPanel";
import type { ClauseActionOut } from "@/lib/api";
import { toast } from "sonner";
import { useUiChrome } from "@/components/UiChrome";
import { answer, createContract, replicateContract, getContract, getVersions, getWorkspace, getClauseText, readWorkspaceFile, streamRun, extractClauses, renderClauseForContract } from "@/lib/api";
import type { ContractVersion, Question, RunEvent, Todo, TraceEntry } from "@/lib/types";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { createExport, downloadUrl, saveDocument } from "@/lib/api";

type Phase = "idle" | "running" | "asking" | "done" | "failed";

const EXAMPLE = "Draft an NDA between ABC Pvt Ltd and XYZ Pvt Ltd under Indian law, courts at Mumbai.";

const SunSpinner = ({ className = "h-5 w-5" }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className={`${className} animate-spin-slow`}>
    {[...Array(12)].map((_, i) => {
      const angle = (i * 30 * Math.PI) / 180;
      const x1 = (12 + 4 * Math.cos(angle)).toFixed(4);
      const y1 = (12 + 4 * Math.sin(angle)).toFixed(4);
      const x2 = (12 + 8 * Math.cos(angle)).toFixed(4);
      const y2 = (12 + 8 * Math.sin(angle)).toFixed(4);
      return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} />;
    })}
  </svg>
);

const PlusIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
    <line x1="12" y1="5" x2="12" y2="19" />
    <line x1="5" y1="12" x2="19" y2="12" />
  </svg>
);

const DownloadIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
    <path d="M12 3v12" />
    <polyline points="7 10 12 15 17 10" />
    <path d="M5 19h14" />
  </svg>
);

const WorkflowIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
    <circle cx="12" cy="12" r="3" />
    <circle cx="6" cy="6" r="3" />
    <circle cx="18" cy="6" r="3" />
    <circle cx="18" cy="18" r="3" />
    <line x1="8" y1="8" x2="10" y2="10" />
    <line x1="16" y1="8" x2="14" y2="10" />
    <line x1="16" y1="16" x2="14" y2="14" />
  </svg>
);

const SendArrow = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
    <line x1="5" y1="12" x2="19" y2="12" />
    <polyline points="12 5 19 12 12 19" />
  </svg>
);

const ChevronDownIcon = ({ className = "h-4 w-4" }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
    <polyline points="6 9 12 15 18 9" />
  </svg>
);

const AssistantIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
    <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
  </svg>
);

const FillIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
    <path d="M9 11l3 3L22 4" />
    <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
  </svg>
);

export default function Page() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [request, setRequest] = useState(EXAMPLE);
  const [files, setFiles] = useState<File[]>([]);
  const [asTemplate, setAsTemplate] = useState(false);
  const [copies, setCopies] = useState(1);
  const [contractType, setContractType] = useState<string | null>(null);
  const [contractId, setContractId] = useState<string | null>(null);
  const [todos, setTodos] = useState<Todo[]>([]);
  const [trace, setTrace] = useState<TraceEntry[]>([]);
  const [questions, setQuestions] = useState<Question[]>([]);
  const [message, setMessage] = useState("");
  const [version, setVersion] = useState<ContractVersion | null>(null);
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<any | null>(null);
  const [selectedClauses, setSelectedClauses] = useState<string[]>([]);
  const [clauseDetails, setClauseDetails] = useState<Record<string, { title: string; body: string }>>({});
  const [loadingClauseDetails, setLoadingClauseDetails] = useState(false);
  const [selectionConfirmed, setSelectionConfirmed] = useState(false);
  
  // Custom redesign states
  const [citationsOpen, setCitationsOpen] = useState(false);
  const [stepsExpanded, setStepsExpanded] = useState(true);
  const [downloading, setDownloading] = useState(false);
  const [analyzingFiles, setAnalyzingFiles] = useState(false);

  // Editing the draft, inserting clauses, and editing the playbook.
  const [editing, setEditing] = useState(false);
  const [editedMarkdown, setEditedMarkdown] = useState("");
  const [clausePickerOpen, setClausePickerOpen] = useState(false);

  // Click-anywhere-in-the-document menu: insert or remove a clause at the clicked spot,
  // without switching into the raw-text "Edit" mode first.
  const [blockMenu, setBlockMenu] = useState<{ x: number; y: number; start: number; end: number } | null>(null);
  const [blockClausePickerOpen, setBlockClausePickerOpen] = useState(false);
  // Whether the open ClauseInserter picker should splice its result before or after the
  // section resolved from blockMenu.start (menuClauseSection). "after" is the default —
  // the right-click menu's "Insert clause after" and the old toolbar Insert button both
  // want that; the hover toolbar's Insert Above button is the only "before" caller.
  const [insertClauseMode, setInsertClauseMode] = useState<"before" | "after">("after");
  // Which menu item is keyboard-focused, for arrow-key navigation within the clause menu.
  const [menuFocusIndex, setMenuFocusIndex] = useState(0);
  const menuRef = useRef<HTMLDivElement>(null);
  // The clause section the open menu's actions apply to, resolved once when the menu opens
  // so Edit/Fill/Remove all act on the same section even if editedMarkdown changes under them.
  const [menuClauseSection, setMenuClauseSection] = useState<ClauseSection | null>(null);
  // Set by "Edit clause" / "Fill details" — consumed by the modals added in later stages.
  // Left in place now so the menu can wire real (not fake) handlers this stage.
  const [editClauseTarget, setEditClauseTarget] = useState<ClauseSection | null>(null);
  const [fillClauseTarget, setFillClauseTarget] = useState<ClauseSection | null>(null);
  const [removeClauseTarget, setRemoveClauseTarget] = useState<ClauseSection | null>(null);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [fillAllOpen, setFillAllOpen] = useState(false);
  const [intel, setIntel] = useState<ContractAnalysis | null>(null);
  const [intelLoading, setIntelLoading] = useState(false);
  const [perspective, setPerspective] = useState<NegotiationPerspective>("neutral");
  const [removing, setRemoving] = useState(false);
  // Colab-style hover toolbar (Preview mode only): which clause's box is currently
  // hovered/highlighted. Re-derived from `markdown` on every render via
  // parseClauseSections, so a clause inserted through ClauseInserter/the assistant/the
  // right-click menu gets a box + hover toolbar automatically the next render — there is
  // no separate "known clauses" list to keep in sync.
  const [hoveredClauseId, setHoveredClauseId] = useState<string | null>(null);
  // Client-side cache of the Contract Variable Memory (`Contract.variables`, alias-resolved
  // server-side). Loaded once when a contract's document is loaded and refreshed after every
  // Fill-details Apply, so Insert/AI-edit/Fill-all all read from the same up-to-date set
  // instead of each re-fetching it — this is the "never ask twice" state.
  const [contractVariables, setContractVariables] = useState<Record<string, string>>({});
  // One-shot undo snapshot for the last clause removal, cleared once used or replaced by a
  // newer removal. Holding the full previous doc (not a diff) keeps the restore trivial and
  // correct regardless of what renumbering/splicing happened.
  const undoSnapshotRef = useRef<{ markdown: string | null; editedMarkdown: string } | null>(null);
  // Tracks what's actually persisted server-side, so the autosave effect below only fires on
  // genuine edits (clause insert/remove/fill, WYSIWYG "Done") rather than on the initial
  // setMarkdown() from loadPreview, or after an autosave has just resolved.
  const lastSavedMarkdownRef = useRef<string | null>(null);
  
  const fileInputRef = useRef<HTMLInputElement>(null);
  const abort = useRef<AbortController | null>(null);

  const { openTarget, clearOpenTarget, refreshContracts, newChatSignal } = useUiChrome();

  useEffect(() => () => abort.current?.abort(), []);

  // "New chat" — reset the whole conversation back to a blank draft. Skips the initial
  // mount (signal starts at 0); every later bump clears state and stops any live stream.
  useEffect(() => {
    if (newChatSignal === 0) return;
    abort.current?.abort();
    setPhase("idle");
    setRequest("");
    setFiles([]);
    setAsTemplate(false);
    setCopies(1);
    setContractId(null);
    setTodos([]);
    setTrace([]);
    setQuestions([]);
    setMessage("");
    setVersion(null);
    setMarkdown(null);
    setError(null);
    setAnalysis(null);
    setSelectedClauses([]);
    setClauseDetails({});
    setSelectionConfirmed(false);
    setEditing(false);
    setEditedMarkdown("");
    setClausePickerOpen(false);
  }, [newChatSignal]);

  // Open a past contract chosen from the sidebar history/projects — load its finished draft.
  useEffect(() => {
    if (!openTarget) return;
    abort.current?.abort();
    setContractId(openTarget.id);
    setRequest(openTarget.request);
    setTrace([]);
    setTodos([]);
    setQuestions([]);
    setError(null);
    setMarkdown(null);
    setVersion(null);
    setAnalysis(null);
    setPhase("done"); // triggers the preview loader keyed on [contractId, phase]
    clearOpenTarget();
  }, [openTarget, clearOpenTarget]);

  // Keep the history/projects list current when a run finishes.
  useEffect(() => {
    if (phase === "done" || phase === "failed") refreshContracts();
  }, [phase, refreshContracts]);

  // Contract Intelligence Engine: one debounced fetch per document version, shared by every
  // widget (health card, outline, risk borders, suggestions, explain). Never fires on every
  // keystroke — waits 900ms after the document or perspective settles, and the backend also
  // caches by content hash so a repeat call for the same document is free.
  useEffect(() => {
    if (!contractId || phase !== "done" || !markdown) {
      setIntel(null);
      return;
    }
    const doc = markdown;
    const handle = setTimeout(() => {
      setIntelLoading(true);
      analyzeContract(contractId, doc, perspective)
        .then(setIntel)
        .catch(() => setIntel(null))
        .finally(() => setIntelLoading(false));
    }, 900);
    return () => clearTimeout(handle);
  }, [contractId, phase, markdown, perspective]);

  function scrollToClauseTitle(title: string) {
    const el = document.querySelector(`[data-clause-title="${CSS.escape(title)}"]`);
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  // Opens the block menu at (x, y) for the [start, end) block, resolving which clause
  // section (if any) the click landed in exactly once — so Edit/Fill/Remove all act on the
  // same section for the lifetime of this menu, even if the document changes under them
  // while the menu is open (e.g. an in-flight "Ask assistant" fill).
  function openBlockMenu(x: number, y: number, start: number, end: number) {
    const doc = editing ? editedMarkdown : markdown;
    setMenuClauseSection(doc ? clauseSectionAt(doc, start) : null);
    setMenuFocusIndex(0);
    setBlockClausePickerOpen(false);
    setInsertClauseMode("after");
    setBlockMenu({ x, y, start, end });
  }

  // Opens the "Insert clause" picker anchored right after a given clause section, from the
  // Colab-style hover toolbar's "Insert Below" button. Reuses the existing
  // blockMenu/ClauseInserter plumbing (insertClauseAtBlock resolves "after" via
  // clauseSectionAt(start), so passing the section's own start is enough) rather than
  // duplicating the picker UI.
  function openInsertAfterClause(e: React.MouseEvent, section: ClauseSection) {
    e.stopPropagation();
    setMenuClauseSection(section);
    setInsertClauseMode("after");
    setBlockMenu({ x: e.clientX, y: e.clientY, start: section.start, end: section.end });
    setBlockClausePickerOpen(true);
  }

  // Same picker, but for the hover toolbar's "Insert Above" button — splices the chosen
  // clause in immediately BEFORE this section instead of after it. Reuses the same
  // ClauseInserter/insertClauseAtBlock plumbing; only insertClauseMode differs.
  function openInsertBeforeClause(e: React.MouseEvent, section: ClauseSection) {
    e.stopPropagation();
    setMenuClauseSection(section);
    setInsertClauseMode("before");
    setBlockMenu({ x: e.clientX, y: e.clientY, start: section.start, end: section.end });
    setBlockClausePickerOpen(true);
  }

  // Opens the clause menu at the block that was clicked. `node` is remark's AST node —
  // its `position.offset`s point into the raw markdown string, which is exactly what's
  // needed to splice an insert or a removal into `markdown` at the right spot.
  // Right-click (contextmenu) is the primary trigger — it's the natural gesture for a
  // context menu — so the browser's own right-click menu is suppressed in favor of ours.
  // Left-click keeps working too, for anyone used to that instead.
  function onDocBlockClick(e: React.MouseEvent, node: any) {
    e.preventDefault();
    e.stopPropagation();
    const start = node?.position?.start?.offset;
    const end = node?.position?.end?.offset;
    if (start == null || end == null) return;
    openBlockMenu(e.clientX, e.clientY, start, end);
  }

  // Same click-anywhere menu as Preview, but for the WYSIWYG Editing view: each rendered
  // block already carries its own [start, end) offsets into `editedMarkdown` (see
  // parseEditBlocks), so opening the menu just needs the block that was clicked — no caret
  // math needed. stopPropagation (not preventDefault) so the click still places the text
  // caret normally for typing, alongside opening the menu.
  function onEditBlockClick(e: React.MouseEvent, block: EditBlock) {
    e.stopPropagation();
    openBlockMenu(e.clientX, e.clientY, block.start, block.end);
  }

  // Right-click variant: also suppresses the browser's native context menu, since here
  // (unlike a plain left-click) there's no reason to preserve default browser behaviour.
  function onEditBlockContextMenu(e: React.MouseEvent, block: EditBlock) {
    e.preventDefault();
    onEditBlockClick(e, block);
  }

  type EditBlock = { start: number; end: number; kind: "h1" | "h2" | "h3" | "blockquote" | "p"; text: string };

  // Split `editedMarkdown` into the same block units Preview renders (blank-line-separated),
  // each carrying its offsets in the full string plus its display text with the markdown
  // marker (#, ##, >) stripped off — so the block can be rendered as a real heading/paragraph
  // element instead of showing literal "##" characters while editing.
  function parseEditBlocks(doc: string): EditBlock[] {
    const blocks: EditBlock[] = [];
    const push = (start: number, end: number) => {
      const raw = doc.slice(start, end);
      if (!raw.trim()) return;
      let kind: EditBlock["kind"] = "p";
      let text = raw;
      const h = !raw.includes("\n") ? raw.match(/^(#{1,3})[ \t]+(.*)$/) : null;
      if (h) {
        kind = h[1].length === 1 ? "h1" : h[1].length === 2 ? "h2" : "h3";
        text = h[2];
      } else if (/^>[ \t]?/.test(raw)) {
        kind = "blockquote";
        text = raw.replace(/^>[ \t]?/gm, "");
      }
      blocks.push({ start, end, kind, text });
    };
    const sepRe = /\n{2,}/g;
    let last = 0;
    let m: RegExpExecArray | null;
    while ((m = sepRe.exec(doc))) {
      push(last, m.index);
      last = m.index + m[0].length;
    }
    push(last, doc.length);
    return blocks;
  }

  function blockRaw(kind: EditBlock["kind"], text: string): string {
    if (kind === "h1") return `# ${text}`;
    if (kind === "h2") return `## ${text}`;
    if (kind === "h3") return `### ${text}`;
    if (kind === "blockquote") return text.split("\n").map((l) => `> ${l}`).join("\n");
    return text;
  }

  // Commit one block's edited text back into editedMarkdown at its own offsets, once the
  // user clicks away (onBlur) — not on every keystroke, so the contentEditable DOM is never
  // fought by a React re-render while it still has focus and a live caret.
  function commitBlockEdit(block: EditBlock, newText: string) {
    const raw = blockRaw(block.kind, newText);
    if (raw === editedMarkdown.slice(block.start, block.end)) return;
    const next = editedMarkdown.slice(0, block.start) + raw + editedMarkdown.slice(block.end);
    setEditedMarkdown(next);
  }

  const markdownComponents: Record<string, React.ComponentType<any>> = {
    h1: ({ node, ...props }) => <h1 className="doc-heading mt-0 text-[32px] font-bold tracking-tight text-[color:var(--text)] cursor-pointer" onClick={(e: React.MouseEvent) => onDocBlockClick(e, node)} onContextMenu={(e: React.MouseEvent) => onDocBlockClick(e, node)} {...props} />,
    h2: ({ node, ...props }) => <h2 className="doc-heading mt-8 text-2xl font-semibold tracking-tight text-[color:var(--text)] cursor-pointer" onClick={(e: React.MouseEvent) => onDocBlockClick(e, node)} onContextMenu={(e: React.MouseEvent) => onDocBlockClick(e, node)} {...props} />,
    h3: ({ node, ...props }) => <h3 className="doc-heading mt-6 text-lg font-semibold tracking-tight text-[color:var(--text)] cursor-pointer" onClick={(e: React.MouseEvent) => onDocBlockClick(e, node)} onContextMenu={(e: React.MouseEvent) => onDocBlockClick(e, node)} {...props} />,
    p: ({ node, ...props }) => <p className="doc-paragraph mt-4 leading-8 text-sm text-[color:var(--text)] cursor-pointer" onClick={(e: React.MouseEvent) => onDocBlockClick(e, node)} onContextMenu={(e: React.MouseEvent) => onDocBlockClick(e, node)} {...props} />,
    strong: ({ node, ...props }) => <strong className="font-semibold" {...props} />,
    em: ({ node, ...props }) => <em className="italic" {...props} />,
    ul: ({ node, ...props }) => <ul className="doc-list list-disc pl-6 mt-4 space-y-2" {...props} />,
    ol: ({ node, ...props }) => <ol className="doc-list list-decimal pl-6 mt-4 space-y-2" {...props} />,
    li: ({ node, ordered, ...props }) => <li className="mt-2 leading-7 cursor-pointer" onClick={(e: React.MouseEvent) => onDocBlockClick(e, node)} onContextMenu={(e: React.MouseEvent) => onDocBlockClick(e, node)} {...props} />,
    blockquote: ({ node, ...props }) => <blockquote className="doc-blockquote mt-6 rounded-3xl border-l-4 border-[color:var(--accent-soft)] bg-[rgba(15,118,110,0.05)] px-5 py-4 italic text-sm leading-7 cursor-pointer" onClick={(e: React.MouseEvent) => onDocBlockClick(e, node)} onContextMenu={(e: React.MouseEvent) => onDocBlockClick(e, node)} {...props} />,
    code: ({ node, inline, className, children, ...props }) => inline ? <code className="doc-inline-code rounded-sm bg-[rgba(15,118,110,0.1)] px-1 py-0.5 text-sm" {...props}>{children}</code> : <pre className="doc-code my-6 overflow-auto rounded-3xl bg-[rgba(15,118,110,0.08)] p-5 text-sm leading-7" {...props}><code>{children}</code></pre>,
    a: ({ node, ...props }) => <a className="theme-link underline decoration-[color:var(--accent)] decoration-2 underline-offset-4" {...props} />,
  };

  // The clause boundaries in the doc are its "## Heading" sections — the same shape every
  // rendered clause takes. Used to answer "which clause did the user click inside" for the
  // Remove option, without needing every clause's own start/end tracked separately.
  // Thin wrapper over the shared parser in lib/clauses.ts, which is now the single source
  // of truth for clause boundaries (context menu, edit, remove, fill-details, insertion
  // positioning, title lookup, and assistant mutations all go through it).
  function clauseSectionAt(doc: string, offset: number): ClauseSection | null {
    return sharedClauseSectionAt(doc, offset);
  }

  // Splice an auto-filled clause in right after the block (Preview) or caret (Editing)
  // that was clicked.
  // Clause headings carry their number as literal text ("## 1. Scope of Services"), not
  // OOXML auto-numbering, so nothing renumbers them on its own once a clause is spliced in
  // or dropped. Matches EVERY "## ..." heading, whether or not it already has a leading
  // number — a freshly-inserted library clause comes in as plain "## Confidentiality" (see
  // ClauseInserter), so it needs a number assigned, not just the existing ones shifted.
  // Renumbers in document order starting at 1, so the result is correct regardless of what
  // the old numbers were.
  // Thin wrapper over the shared implementation in lib/clauses.ts — see clauseSectionAt
  // above for why this stays a single source of truth (assistant-proposed inserts now
  // renumber through the same function).
  function renumberClauseHeadings(doc: string): string {
    return sharedRenumberClauseHeadings(doc);
  }

  // Splice an auto-filled clause in right before or after the *whole clause section* the
  // clicked block belongs to — not just adjacent to that one block. Direction is
  // insertClauseMode: "after" (Insert Below / the right-click "Insert clause after" menu
  // item / the classic +Insert button) splices right after the section end; "before"
  // (Insert Above) splices right at the section start. blockMenu's offsets are correct for
  // whichever view was clicked (Preview offsets into `markdown`, Editing offsets into
  // `editedMarkdown` — see onDocBlockClick / onEditBlockClick), so the source string to
  // splice into has to match: reading the wrong one silently applies right offsets to the
  // wrong string, which is how a neighboring clause used to get swallowed or corrupted.
  // Falls back to the clicked block's own end when the click landed before any "## " clause
  // heading (e.g. in the document's opening paragraph), since there's no section to extend —
  // "before" has no meaningful fallback there either, so it also uses the block end.
  function insertClauseAtBlock(snippet: string) {
    if (!blockMenu) return;
    const doc = editing ? editedMarkdown : markdown;
    if (doc == null) return;
    const section = clauseSectionAt(doc, blockMenu.start);
    const insertAt = section ? (insertClauseMode === "before" ? section.start : section.end) : blockMenu.end;
    const resolvedSnippet = resolveKnownPlaceholders(snippet, contractVariables);
    const spliced = doc.slice(0, insertAt) + resolvedSnippet + doc.slice(insertAt);
    const next = renumberClauseHeadings(spliced);
    setMarkdown(next);
    setEditedMarkdown(next);
    setBlockMenu(null);
    setBlockClausePickerOpen(false);
  }

  // Removes the clause the confirmation dialog is open for. Snapshots the pre-removal
  // document first so the toast's Undo action can restore it exactly; the snapshot is
  // one-shot and gets overwritten by the next removal (only the most recent remove is
  // undoable, matching "temporary Undo action using the previous document state").
  function confirmRemoveClause() {
    if (!removeClauseTarget || removing) return; // guards against a double-remove
    const doc = editing ? editedMarkdown : markdown;
    if (doc == null) return;
    const target = removeClauseTarget;
    if (doc.slice(target.start, target.end) !== target.markdown) {
      toast.error("The document changed — please reopen this clause and try again.");
      setRemoveClauseTarget(null);
      return;
    }
    setRemoving(true);
    undoSnapshotRef.current = { markdown, editedMarkdown };
    const spliced = (doc.slice(0, target.start) + doc.slice(target.end)).replace(/\n{3,}/g, "\n\n");
    const next = renumberClauseHeadings(spliced);
    setMarkdown(next);
    setEditedMarkdown(next);
    setRemoveClauseTarget(null);
    setBlockMenu(null);
    setBlockClausePickerOpen(false);
    setRemoving(false);
    const removedTitle = target.title || "Untitled clause";
    toast.success(`Removed "${removedTitle}".`, {
      action: {
        label: "Undo",
        onClick: () => {
          const snapshot = undoSnapshotRef.current;
          if (!snapshot) return;
          setMarkdown(snapshot.markdown);
          setEditedMarkdown(snapshot.editedMarkdown);
          undoSnapshotRef.current = null;
        },
      },
    });
  }

  // Saving an edited clause replaces only that section, using the exact offsets captured
  // when the clause menu was opened (editClauseTarget) rather than re-resolving by
  // instanceId — the document is not expected to change while this modal is open (it's
  // modal/blocking), so the captured offsets remain valid and this avoids a silent no-op if
  // a re-parse ever produced a different instanceId ordering.
  function onSaveEditClause(nextSectionMarkdown: string) {
    if (!editClauseTarget) return;
    const doc = editing ? editedMarkdown : markdown;
    if (doc == null) return;
    const target = editClauseTarget;
    // Re-validate the target is still where we think it is before splicing — if the document
    // shifted unexpectedly, refuse rather than corrupt an unrelated clause.
    if (doc.slice(target.start, target.end) !== target.markdown) {
      toast.error("The document changed while editing — please reopen this clause and try again.");
      setEditClauseTarget(null);
      return;
    }
    const resolved = resolveKnownPlaceholders(nextSectionMarkdown, contractVariables);
    const spliced = doc.slice(0, target.start) + resolved + doc.slice(target.end);
    const next = renumberClauseHeadings(spliced);
    setMarkdown(next);
    setEditedMarkdown(next);
    setEditClauseTarget(null);
    toast.success("Clause updated.");
  }

  // Applying Fill-details replaces only the targeted clause, same splice+renumber+staleness
  // pattern as edit and remove. `contractVariables` (below) is the contract's real resolved
  // Variable Memory — loaded on mount and refreshed after every modal Apply — so a value
  // filled in once is available to every later clause without a per-clause round trip.
  const knownContractValues = contractVariables;

  // Passed to the Fill-details modals; called with the full merged variable set right after
  // they persist newly-typed values via `updateContractVariables`. Replaces the cache rather
  // than patching it, so it can never drift from what the backend actually stored.
  function onVariablesPersisted(variables: Record<string, string>) {
    setContractVariables(variables);
  }

  function onApplyFillDetails(nextSectionMarkdown: string) {
    if (!fillClauseTarget) return;
    const doc = editing ? editedMarkdown : markdown;
    if (doc == null) return;
    const target = fillClauseTarget;
    if (doc.slice(target.start, target.end) !== target.markdown) {
      toast.error("The document changed while filling details — please reopen this clause and try again.");
      setFillClauseTarget(null);
      return;
    }
    const spliced = doc.slice(0, target.start) + nextSectionMarkdown + doc.slice(target.end);
    const next = renumberClauseHeadings(spliced);
    setMarkdown(next);
    setEditedMarkdown(next);
    setFillClauseTarget(null);
    toast.success("Clause details applied.");
  }

  // Applies one assistant-proposed action to the document, through the same splice
  // functions the manual context menu uses (insert/replace/remove/fill-details) — the
  // assistant only chooses targets and library clause ids, never authors clause text.
  // Throws with a user-facing message on a stale/ambiguous/missing target so
  // AssistantPanel can surface it inline rather than silently doing nothing.
  async function applyClauseAction(action: ClauseActionOut) {
    const doc = editing ? editedMarkdown : markdown;
    if (doc == null) throw new Error("No document to edit yet.");

    if (action.action === "insert") {
      if (!contractId) throw new Error("No contract to render this clause for.");
      const rendered = await renderClauseForContract(contractId, action.clause_id);
      if (action.after_clause_title) {
        const target = findClauseByTitle(doc, action.after_clause_title);
        if (target === "ambiguous") {
          throw new Error(`"${action.after_clause_title}" matches more than one clause — rename one first.`);
        }
        if (target === null) {
          throw new Error(`Couldn't find a clause titled "${action.after_clause_title}".`);
        }
      }
      const snippet = `\n\n## ${rendered.title}\n\n${rendered.text}\n`;
      const next = insertClauseSection(doc, snippet, action.after_clause_title || null);
      setMarkdown(next);
      setEditedMarkdown(next);
      toast.success("Clause inserted.");
      return;
    }

    if (action.action === "replace") {
      if (!contractId) throw new Error("No contract to render this clause for.");
      const target = findClauseByTitle(doc, action.clause_title);
      if (target === "ambiguous") {
        throw new Error(`"${action.clause_title}" matches more than one clause — rename one first.`);
      }
      if (target === null) {
        throw new Error(`Couldn't find a clause titled "${action.clause_title}".`);
      }
      const rendered = await renderClauseForContract(contractId, action.clause_id);
      const snippet = `## ${rendered.title}\n\n${rendered.text}\n`;
      const next = sharedRenumberClauseHeadings(sharedReplaceClauseSection(doc, target.instanceId, snippet));
      setMarkdown(next);
      setEditedMarkdown(next);
      toast.success("Clause replaced.");
      return;
    }

    if (action.action === "remove") {
      const target = findClauseByTitle(doc, action.clause_title);
      if (target === "ambiguous") {
        throw new Error(`"${action.clause_title}" matches more than one clause — rename one first.`);
      }
      if (target === null) {
        throw new Error(`Couldn't find a clause titled "${action.clause_title}".`);
      }
      const next = sharedRemoveClauseSection(doc, target.instanceId);
      setMarkdown(next);
      setEditedMarkdown(next);
      toast.success("Clause removed.");
      return;
    }

    if (action.action === "fill") {
      const target = findClauseByTitle(doc, action.clause_title);
      if (target === "ambiguous") {
        throw new Error(`"${action.clause_title}" matches more than one clause — rename one first.`);
      }
      if (target === null) {
        throw new Error(`Couldn't find a clause titled "${action.clause_title}".`);
      }
      const fields = detectPlaceholders(target.markdown);
      const { markdown: filled, remaining } = applyPlaceholderValues(target.markdown, fields, action.fields);
      const next = sharedRenumberClauseHeadings(sharedReplaceClauseSection(doc, target.instanceId, filled));
      setMarkdown(next);
      setEditedMarkdown(next);
      if (remaining.length > 0) {
        toast.success(`Filled in some details — ${remaining.length} field(s) still need a value.`);
      } else {
        toast.success("Clause details filled in.");
      }
      return;
    }

    throw new Error(`Unrecognised action "${action.action}".`);
  }

  type BlockMenuItem = { key: string; label: string; onSelect: () => void; destructive?: boolean };

  // Order: Edit clause, Fill details, Insert clause above, Insert clause below, Remove
  // clause, then Fill all missing details (document-wide, always offered as a backup
  // entry point to the toolbar's "Fill" button). Edit/Fill/Insert-above/Remove only appear
  // when the click landed inside a valid clause section; Insert-below and Fill-all are
  // always offered since Insert-below also works from the document's opening paragraph
  // (before any clause exists yet), and Fill-all is document-wide.
  const blockMenuItems: BlockMenuItem[] = [
    ...(menuClauseSection
      ? [
          {
            key: "edit",
            label: "Edit clause",
            onSelect: () => {
              setEditClauseTarget(menuClauseSection);
              setBlockMenu(null);
            },
          },
          {
            key: "fill",
            label: "Fill details",
            onSelect: () => {
              setFillClauseTarget(menuClauseSection);
              setBlockMenu(null);
            },
          },
        ]
      : []),
    ...(menuClauseSection
      ? [
          {
            key: "insert-above",
            label: "Insert clause above",
            onSelect: () => {
              setInsertClauseMode("before");
              setBlockClausePickerOpen(true);
            },
          },
        ]
      : []),
    {
      key: "insert-below",
      label: menuClauseSection ? "Insert clause below" : "Insert clause after",
      onSelect: () => {
        setInsertClauseMode("after");
        setBlockClausePickerOpen(true);
      },
    },
    ...(menuClauseSection
      ? [
          {
            key: "remove",
            label: "Remove clause",
            destructive: true,
            onSelect: () => {
              setRemoveClauseTarget(menuClauseSection);
              setBlockMenu(null);
            },
          },
        ]
      : []),
    {
      key: "fill-all",
      label: "Fill all missing details…",
      onSelect: () => {
        setFillAllOpen(true);
        setBlockMenu(null);
      },
    },
  ];

  const blockMenuItemRefs = useRef<(HTMLButtonElement | null)[]>([]);

  // Arrow-key navigation within the open menu; Escape closes it. Enter/Space activate the
  // focused item via the button's own native behaviour, so only movement is handled here.
  function onBlockMenuKeyDown(e: React.KeyboardEvent) {
    if (blockClausePickerOpen) return; // ClauseInserter manages its own key handling.
    if (e.key === "Escape") {
      e.stopPropagation();
      setBlockMenu(null);
      return;
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const count = blockMenuItems.length + 1; // + Cancel
      const next = e.key === "ArrowDown"
        ? (menuFocusIndex + 1) % count
        : (menuFocusIndex - 1 + count) % count;
      setMenuFocusIndex(next);
      blockMenuItemRefs.current[next]?.focus();
    }
  }

  // Keep the menu inside the viewport: once it's rendered and its real size is known,
  // clamp its fixed top/left so it doesn't overflow the right or bottom edge.
  useEffect(() => {
    if (!blockMenu || !menuRef.current) return;
    const rect = menuRef.current.getBoundingClientRect();
    const margin = 8;
    let { x, y } = blockMenu;
    let changed = false;
    if (rect.right > window.innerWidth - margin) {
      x = Math.max(margin, window.innerWidth - rect.width - margin);
      changed = true;
    }
    if (rect.bottom > window.innerHeight - margin) {
      y = Math.max(margin, window.innerHeight - rect.height - margin);
      changed = true;
    }
    if (changed) setBlockMenu((cur) => (cur ? { ...cur, x, y } : cur));
    // Focus the first menu item so Escape/ArrowDown work immediately without a prior click.
    blockMenuItemRefs.current[0]?.focus();
    // Only re-run when the menu (re)opens or the picker step changes, not on every
    // blockMenu.x/y write this effect itself may cause — position clamping is idempotent
    // (a second pass computes the same already-in-bounds rect and skips the update).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [blockMenu?.start, blockMenu?.end, blockClausePickerOpen]);

  // Walk every top-level "## ..." clause heading in document order and reassign sequential
  // numbers ("## 3. Confidentiality" -> "## 4. Confidentiality"), stripping whatever number
  // (or none, for a freshly-inserted library clause) was there before. Keeps clause numbering
  // consistent after any insert/remove instead of leaving a gap or duplicate.

  async function loadClauseDetails(ids: string[]) {
    const missingIds = ids.filter((id) => !clauseDetails[id]);
    if (missingIds.length === 0) return;
    setLoadingClauseDetails(true);

    try {
      const details = await Promise.all(missingIds.map(async (id) => {
        try {
          const payload = await getClauseText(id) as any;
          return { id, title: payload?.title ?? id, body: String(payload?.body ?? "") };
        } catch {
          return { id, title: id, body: "" };
        }
      }));

      setClauseDetails((current) => {
        const next = { ...current };
        for (const detail of details) {
          next[detail.id] = { title: detail.title, body: detail.body };
        }
        return next;
      });
    } finally {
      setLoadingClauseDetails(false);
    }
  }

  const onEvent = useCallback((event: RunEvent) => {
    switch (event.event) {
      case "todo_update": setTodos(event.data.todos as Todo[]); break;
      // The pipeline reports progress as named stages rather than tool calls. A `started`
      // stage opens a step; its `complete`/`done` counterpart closes the same one.
      case "stage": {
        const stage = event.data.stage as string;
        const status = String(event.data.status ?? "");
        if (status === "started") {
          setTrace((current) => [...current, { seq: event.seq, tool: stage, agent: "pipeline" }]);
        } else {
          setTrace((current) => {
            const next = [...current];
            for (let i = next.length - 1; i >= 0; i -= 1) if (next[i].tool === stage && next[i].output === undefined) {
              next[i] = { ...next[i], output: status }; break;
            }
            return next;
          });
        }
        break;
      }
      case "tool_call": setTrace((current) => [...current, { seq: event.seq, tool: event.data.tool as string, agent: event.data.agent as string }]); break;
      case "tool_result": setTrace((current) => {
        const next = [...current];
        for (let i = next.length - 1; i >= 0; i -= 1) if (next[i].tool === event.data.tool && next[i].output === undefined) {
          next[i] = { ...next[i], output: event.data.output as string, truncated: event.data.truncated as boolean }; break;
        }
        return next;
      }); break;
      case "input_required": setQuestions((event.data.questions ?? []) as Question[]); setPhase("asking"); break;
      case "complete": setMessage(event.data.message as string); setPhase("done"); break;
      // The server sends `reason` (an exception class name) for a fault and `message` for a
      // reported failure. Prefer whichever is present so the panel is never blank.
      case "error": setMessage((event.data.message as string) ?? (event.data.reason ? `The run failed (${event.data.reason}).` : "The run failed.")); setPhase("failed"); break;
    }
  }, []);

  const follow = useCallback(async (runId: string) => {
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    await streamRun(runId, 0, onEvent, controller.signal);
  }, [onEvent]);

  async function start() {
    setError(null); setTodos([]); setTrace([]); setQuestions([]); setVersion(null); setMarkdown(null); setPhase("running");
    try {
      const clauseIdsToSend = selectionConfirmed ? selectedClauses : [];
      const useTemplate = asTemplate && files.length > 0;

      // More than one copy is a different endpoint: it starts several runs and returns them
      // all. We follow the first so the page shows progress; the rest appear in history.
      if (useTemplate && copies > 1) {
        const batch = await replicateContract(request, files[0], copies);
        const [first] = batch.contracts;
        refreshContracts();
        setContractId(first.contract_id);
        await follow(first.run_id);
        return;
      }

      const run = await createContract(request, files, clauseIdsToSend, useTemplate);
      setContractId(run.contract_id);
      await follow(run.run_id);
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); setPhase("failed"); }
  }

  async function submitAnswers(answers: Record<string, string>) {
    if (!contractId) return;
    setPhase("running"); setQuestions([]);
    try { const accepted = await answer(contractId, answers); await follow(accepted.run_id); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); setPhase("failed"); }
  }

  async function download() {
    if (!contractId) return;
    setDownloading(true); setError(null);
    try {
      const info = await createExport(contractId);
      window.location.href = downloadUrl(info.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDownloading(false);
    }
  }

  async function handleAnalyzeFiles() {
    if (files.length === 0) return;
    setAnalyzingFiles(true);
    setError(null);
    try {
      const res = await extractClauses(files, request, contractId ?? undefined) as any;
      setAnalysis(res);
      if (res?.matches) {
        const ids = res.matches.map((m: any) => m.clause_id);
        setSelectedClauses(ids);
        await loadClauseDetails(ids);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setAnalyzingFiles(false);
    }
  }

  useEffect(() => {
    if (!contractId || phase === "idle" || phase === "running" || phase === "asking") return;

    const loadPreview = async () => {
      setPreviewLoading(true);
      try {
        const versions = await getVersions(contractId);
        const finalized = versions.find((item) => item.finalized_at != null) ?? null;
        setVersion(finalized ?? versions.at(-1) ?? null);

        // The contract type names the document; it is only known once Phase A has run.
        // The Variable Memory (`contract.variables`) is loaded here too, already alias-
        // resolved server-side, so Fill-details/Insert/AI-edit have it from the start
        // instead of starting empty until the first Fill-details Apply.
        await getContract(contractId)
          .then((c) => {
            setContractType(c.contract_type);
            setContractVariables(c.variables ?? {});
          })
          .catch(() => setContractType(null));

        const workspace = await getWorkspace(contractId);
        const finalPath = workspace.find((file) => file.path === "final.md")?.path;
        const draftPaths = workspace
          .map((file) => file.path)
          .filter((path) => /^draft_v\d+\.md$/.test(path))
          .sort((a, b) => {
            const aMatch = a.match(/draft_v(\d+)\.md/);
            const bMatch = b.match(/draft_v(\d+)\.md/);
            return (Number(bMatch?.[1] ?? 0) - Number(aMatch?.[1] ?? 0));
          });

        const pathToRead = finalPath ?? draftPaths[0];
        if (pathToRead) {
          const content = await readWorkspaceFile(contractId, pathToRead).catch(() => null);
          setMarkdown(content);
          setEditedMarkdown(content ?? "");
          setEditing(false);
          lastSavedMarkdownRef.current = content;
        } else {
          setMarkdown(null);
          lastSavedMarkdownRef.current = null;
        }
      } catch {
        setMarkdown(null);
      } finally {
        setPreviewLoading(false);
      }
    };

    void loadPreview();
  }, [contractId, phase]);

  // Persists edits (clause insert/remove/fill, WYSIWYG edits committed via "Done") back to
  // the backend — previously these only ever lived in this component's React state, so a
  // reload — or worse, Download — silently reverted to the drafting engine's original output.
  // Debounced and keyed off `markdown` specifically (not `editedMarkdown`, which changes on
  // every keystroke while a block is being edited and only lands in `markdown` on "Done").
  useEffect(() => {
    if (!contractId || markdown == null) return;
    if (!version?.finalized_at) return; // nothing to save the document onto yet
    if (markdown === lastSavedMarkdownRef.current) return;

    const handle = setTimeout(() => {
      const toSave = markdown;
      saveDocument(contractId, toSave)
        .then(() => {
          lastSavedMarkdownRef.current = toSave;
        })
        .catch(() => {
          toast.error("Couldn't save your changes — they're still shown here, but a reload may lose them.");
        });
    }, 800);

    return () => clearTimeout(handle);
  }, [contractId, markdown, version?.finalized_at]);

  const busy = phase === "running";
  const isInProgress = phase === "running" || phase === "asking";

  // Toolbar "+ Insert clause" quick button: appends at the end of the document. (For
  // inserting at a specific spot, click directly on the block to insert after — that opens
  // the same picker positioned there.)
  function insertClauseMarkdown(snippet: string) {
    setEditedMarkdown((current) => renumberClauseHeadings(current.trim() + "\n\n" + snippet.trim() + "\n"));
    setClausePickerOpen(false);
  }

  // Readable names for the pipeline's stages and the older path's tool calls. Anything
  // unrecognised is shown as itself rather than mislabelled.
  const labelForStep = (name: string): string => {
    const known: Record<string, string> = {
      planning: "Understanding the request",
      resuming: "Resuming with your answers",
      phase_a: "Gathering knowledge (intent, sources, understanding)",
      phase_b: "Drafting and validating",
      intent: "Reading your request",
      orchestrator: "Drafting",
    };
    if (known[name]) return known[name];
    return name.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
  };

  // The document's name, from what was actually drafted — never a fixed string. Prefers the
  // draft's own H1, then the contract type, so an SLA is never labelled an NDA.
  const docTitle = (() => {
    const heading = markdown?.match(/^#\s+(.+)$/m)?.[1]?.trim();
    if (heading) return heading;
    const named: Record<string, string> = {
      nda: "Non-Disclosure Agreement",
      service: "Services Agreement",
      sla: "Service Level Agreement",
      msa: "Master Services Agreement",
      dpa: "Data Processing Agreement",
    };
    return (contractType && named[contractType]) || "Draft Contract";
  })();

  // Why the gates refused, taken from the run's terminal message ("...blocked: <reasons>").
  const blockReason = (() => {
    const marker = message.indexOf("blocked:");
    if (marker === -1) return null;
    const reason = message.slice(marker + "blocked:".length).trim();
    return reason ? reason.charAt(0).toUpperCase() + reason.slice(1) : null;
  })();

  // The list of fill-in slots, lifted from the run's completion message.
  const reviewNote = (() => {
    const marker = message.indexOf("fill in:");
    return marker === -1 ? null : message.slice(marker + "fill in:".length).trim() || null;
  })();

  // Build step list from trace & todos
  const renderTimelineSteps = () => {
    const steps: { text: string; status: "pending" | "completed" | "active"; isThought?: boolean }[] = [];

    if (trace.length === 0) {
      // No steps reported yet. Say only that, rather than inventing a history.
      steps.push({
        text: phase === "running" ? "Working…" : "No steps recorded",
        status: phase === "running" ? "active" : "completed",
        isThought: true,
      });
    } else {
      trace.forEach((entry, idx) => {
        const isLast = idx === trace.length - 1;
        const completed = entry.output !== undefined;
        steps.push({
          text: labelForStep(entry.tool),
          status: completed || phase !== "running" ? "completed" : isLast ? "active" : "pending",
          isThought: false,
        });
      });
    }

    return (
      <div className="chat-timeline">
        {steps.map((step, i) => (
          <div key={i} className="chat-timeline-item">
            <div className={`chat-timeline-dot ${step.status === "completed" ? "completed" : step.status === "active" ? "active" : ""}`} />
            <div className={`chat-timeline-text ${step.status === "completed" ? "completed" : ""}`}>
              {step.text}
              {step.isThought && <span className="ml-1 text-xs text-[color:var(--text-muted)]">&gt;</span>}
            </div>
          </div>
        ))}
      </div>
    );
  };

  return (
    <main className="flex flex-col min-h-[calc(100vh-2rem)]">
      {/* Clean full screen chat view — grows to fill, pushing the input to the bottom. */}
      <section className="chat-card border-none shadow-none bg-transparent flex-1 pb-6">
        <div className="chat-main p-0 max-w-6xl mx-auto space-y-6">
          
          {/* Welcome assistant message (visible initially) */}
          {phase === "idle" && (
            <div className="flex items-start gap-4">
              <div className="flex-shrink-0 mt-1">
                <div className="h-8 w-8 rounded-full bg-[color:var(--surface-muted)] border border-[color:var(--border)] flex items-center justify-center">
                  <SunSpinner className="h-5 w-5 text-[color:var(--text-muted)] animate-none" />
                </div>
              </div>
              <div className="flex-1">
                <div className="chat-bubble assistant" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
                  Upload reference documents and describe the agreement. I’ll match approved clauses, surface clause details, and help you build a first draft.
                  <div className="chat-meta mt-2 text-xs text-[color:var(--text-muted)]">Clause library, document extraction, semantic matching</div>
                </div>
              </div>
            </div>
          )}

          {/* User message (visible once drafting starts) */}
          {phase !== "idle" && (
            <div className="flex flex-col items-end space-y-1">
              <div className="chat-bubble user" style={{ background: "rgba(15, 118, 110, 0.08)", border: "1px solid rgba(15, 118, 110, 0.12)" }}>
                {request}
              </div>
            </div>
          )}

          {/* Assistant message (visible once running/asking/done/failed) */}
          {phase !== "idle" && (
            <div className="flex items-start gap-4">
              <div className="flex-shrink-0 mt-1">
                <div className="h-8 w-8 rounded-full bg-[color:var(--surface-muted)] border border-[color:var(--border)] flex items-center justify-center">
                  {busy ? (
                    <SunSpinner className="h-5 w-5 text-[color:var(--accent)] animate-spin-slow" />
                  ) : (
                    <SunSpinner className="h-5 w-5 text-[color:var(--text-muted)] animate-none" />
                  )}
                </div>
              </div>

              <div className="flex-1 space-y-4">
                
                {/* Collapsible Steps Stepper Accordion */}
                <div className="rounded-2xl border border-[color:var(--border)] bg-[color:var(--surface)] p-4 shadow-sm">
                  <div 
                    className="flex items-center justify-between cursor-pointer"
                    onClick={() => setStepsExpanded(!stepsExpanded)}
                  >
                    <span className="text-sm font-semibold text-[color:var(--text-muted)]">
                      {phase === "running" ? "Running drafting steps..." : `Completed in ${trace.length || 4} steps`}
                    </span>
                    <ChevronDownIcon className={`h-4 w-4 text-[color:var(--text-muted)] transition-transform ${stepsExpanded ? "" : "-rotate-90"}`} />
                  </div>
                  
                  {stepsExpanded && (
                    <div className="mt-4 pt-3 border-t border-[color:var(--border)]">
                      {renderTimelineSteps()}
                    </div>
                  )}
                </div>

                {/* A draft that the validation gates refused. It is shown — never withheld —
                    but it is not offered as a finished document, and the reason is stated. */}
                {phase === "done" && version && !version.finalized_at && (
                  <div className="doc-blocked-card">
                    <div className="doc-blocked-title">Blocked by validation — not finalized</div>
                    <p className="doc-blocked-reason">
                      {blockReason ??
                        "A validation gate refused this draft. The text below is retained for review."}
                    </p>
                    <p className="doc-blocked-hint">
                      The draft below is kept for review. Address the point above and run the
                      request again to get a signable document.
                    </p>
                  </div>
                )}

                {/* Finalized, but with fill-in placeholders the user should complete. The
                    document is ready and downloadable — this only names what to fill. */}
                {phase === "done" && version && version.finalized_at && version.needs_human_review && (
                  <div className="doc-review-card">
                    <div className="doc-review-title">Ready — a few details to fill in</div>
                    <p className="doc-review-reason">
                      {reviewNote ??
                        "The draft is complete and downloadable. It leaves some values as labelled placeholders for you to fill — search the document for text in [BRACKETS]."}
                    </p>
                    <p className="doc-review-hint">
                      Prefer them filled in automatically? Run the request again with those
                      values included.
                    </p>
                  </div>
                )}

                {/* The finalized docx download card now lives after Document Preview — see below. */}

                {/* Citations section */}
                {phase === "done" && (
                  <div>
                    <div className="citation-divider-container">
                      <div className="citation-divider-line" />
                      <button
                        type="button"
                        className="citation-divider-btn"
                        onClick={() => setCitationsOpen(!citationsOpen)}
                        aria-expanded={citationsOpen}
                      >
                        <ChevronDownIcon className={`h-4 w-4 transition-transform ${citationsOpen ? "rotate-180" : ""}`} />
                      </button>
                    </div>

                    {citationsOpen && (
                      <div className="mt-4 p-4 rounded-2xl border border-[color:var(--border)] bg-[color:var(--surface)] shadow-sm">
                        <h4 className="text-sm font-semibold mb-2">Citations</h4>
                        <div className="flex items-center justify-between text-sm py-2 border-b border-[color:var(--border)]">
                          <div className="flex items-center gap-2">
                            <span className="text-blue-500">📄</span>
                            <span className="font-medium text-[color:var(--text)]">Mutual Non Disclosure Agreement.docx</span>
                          </div>
                          <div className="flex items-center gap-1">
                            {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].map((num) => (
                              <span
                                key={num}
                                className="inline-flex items-center justify-center h-6 w-6 rounded-full bg-[color:var(--surface-muted)] text-[10px] text-[color:var(--text-muted)] cursor-pointer hover:bg-[color:var(--accent-soft)] hover:text-[color:var(--accent-strong)] transition-colors"
                              >
                                {num}
                              </span>
                            ))}
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                )}

              </div>
            </div>
          )}

          {/* Interactive clause suggestions matching list (rendered if analysis exists) */}
          {analysis && analysis.matches && !selectionConfirmed && (
            <div className="glass-panel p-6 max-w-6xl mx-auto">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h3 className="text-base font-semibold">Clause suggestions</h3>
                  <p className="mt-1 text-xs text-[color:var(--text-muted)]">Review matching clauses from the library and include them in the draft.</p>
                </div>
                <button 
                  type="button" 
                  className="text-sm text-[color:var(--accent)] font-semibold"
                  onClick={() => { setAnalysis(null); setSelectedClauses([]); setClauseDetails({}); setSelectionConfirmed(false); }}
                >
                  Clear
                </button>
              </div>

              {loadingClauseDetails && <p className="mt-2 text-xs text-[color:var(--text-muted)]">Fetching clause titles and text…</p>}
              
              <ul className="mt-4 space-y-4">
                {analysis.matches.map((m: any) => {
                  const detail = clauseDetails[m.clause_id];
                  return (
                    <li key={m.clause_id} className="rounded-2xl border border-[color:var(--border)] bg-[color:var(--surface-strong)] p-4">
                      <div className="flex items-start gap-3">
                        <input 
                          type="checkbox" 
                          className="mt-1 accent-[color:var(--accent)]"
                          checked={selectedClauses.includes(m.clause_id)} 
                          onChange={(e) => {
                            if (e.target.checked) setSelectedClauses((s) => Array.from(new Set([...s, m.clause_id])));
                            else setSelectedClauses((s) => s.filter((id) => id !== m.clause_id));
                          }} 
                        />
                        <div className="min-w-0 flex-1">
                          <div className="text-sm font-semibold text-[color:var(--text)]">{detail?.title ?? m.clause_id}</div>
                          <div className="mt-1 flex flex-wrap gap-2 text-xs text-[color:var(--text-muted)]">
                            <span>{m.clause_id}</span>
                            <span>• score {Math.round(m.score)}</span>
                          </div>
                          <p className="mt-2 text-xs text-[color:var(--text-muted)] italic">Snippet: &quot;{m.snippet}&quot;</p>
                        </div>
                      </div>
                      {detail && (
                        <div className="mt-3 rounded-xl border border-[color:var(--border)] bg-[color:var(--surface-muted)] p-3 text-xs leading-5 text-[color:var(--text)] max-h-40 overflow-y-auto font-mono">
                          {detail.body}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
              <div className="mt-4 flex items-center gap-3">
                <button 
                  type="button" 
                  className="btn bg-[color:var(--accent)] text-white px-4 py-2 rounded-xl text-sm font-semibold hover:opacity-90 transition-opacity" 
                  onClick={() => setSelectionConfirmed(true)} 
                  disabled={selectedClauses.length === 0}
                >
                  Include selected in draft
                </button>
                {selectionConfirmed && <span className="text-sm text-[color:var(--text-muted)]">Selections locked in</span>}
              </div>
            </div>
          )}

          {/* Interactive Question Panel for input required */}
          {phase === "asking" && (
            <div className="max-w-6xl mx-auto">
              <Questions questions={questions} onSubmit={submitAnswers} busy={false} />
            </div>
          )}

          {/* Error Message Panel */}
          {error && (
            <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800 max-w-6xl mx-auto shadow-sm">
              {error}
            </div>
          )}

          {/* A run that failed server-side. Without this the stream just stops and the page
              sits there looking idle, which reads as "nothing happened". */}
          {phase === "failed" && !error && (
            <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800 max-w-6xl mx-auto shadow-sm">
              <b>The run stopped before finishing.</b>
              <div className="mt-1">{message || "The server reported an error."}</div>
              <div className="mt-1 text-xs opacity-80">
                Nothing was saved for this attempt. Try running the request again.
              </div>
            </div>
          )}

          {/* Finished, but produced no document and no questions — say so rather than
              rendering an empty page. */}
          {phase === "done" && !markdown && !previewLoading && (
            <div className="rounded-2xl border border-[color:var(--border)] bg-[color:var(--surface)] p-4 text-sm text-[color:var(--text-muted)] max-w-6xl mx-auto shadow-sm">
              <b className="text-[color:var(--text)]">The run finished without producing a document.</b>
              <div className="mt-1">{message || "No draft was recorded for this request."}</div>
            </div>
          )}

          {/* Contract Intelligence Workspace: Outline (left) / editor (center) / Contract
              Intelligence (right) — the right sidebar is always visible once a document
              exists, sharing the single `intel` analysis fetched below. */}
          {phase === "done" && markdown && (
            <div className="workspace-grid mt-8 max-w-[1800px] mx-auto">
              <ContractOutlineSidebar analysis={intel} onScrollToClause={scrollToClauseTitle} />

          {/* Document Preview Card — scrollable, and editable with clause insertion */}
            <div className="glass-panel overflow-hidden min-w-0">
              <div className="border-b border-[color:var(--border)] px-5 py-4 bg-[color:var(--surface-muted)] flex justify-between items-center gap-3 flex-wrap">
                <h3 className="font-semibold text-sm">Document Preview</h3>
                <div className="flex items-center gap-2">
                  {editing && (
                    <button
                      type="button"
                      onClick={() => setClausePickerOpen((v) => !v)}
                      className="text-xs font-semibold px-3 py-1 rounded-full border border-[color:var(--accent-soft)] text-[color:var(--accent-strong)] bg-[color:var(--accent-soft)] hover:opacity-90 transition-opacity"
                    >
                      + Insert clause
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => setAssistantOpen((v) => !v)}
                    aria-pressed={assistantOpen}
                    className={`text-xs font-semibold px-3 py-1 rounded-full border transition-colors flex items-center gap-1.5 ${
                      assistantOpen
                        ? "border-[color:var(--accent-soft)] text-[color:var(--accent-strong)] bg-[color:var(--accent-soft)]"
                        : "border-[color:var(--border)] text-[color:var(--text)] hover:bg-[color:var(--surface-strong)]"
                    }`}
                  >
                    <AssistantIcon />
                    Assistant
                  </button>
                  <button
                    type="button"
                    onClick={() => setFillAllOpen(true)}
                    title="Find and fill every missing detail in the document"
                    className="text-xs font-semibold px-3 py-1 rounded-full border border-[color:var(--border)] text-[color:var(--text)] hover:bg-[color:var(--surface-strong)] transition-colors flex items-center gap-1.5"
                  >
                    <FillIcon />
                    Fill
                  </button>
                  <button
                    type="button"
                    onClick={download}
                    disabled={downloading}
                    title={`Download ${docTitle}.docx`}
                    className="text-xs font-semibold px-3 py-1 rounded-full border border-[color:var(--border)] text-[color:var(--text)] hover:bg-[color:var(--surface-strong)] transition-colors disabled:opacity-50 flex items-center gap-1.5"
                  >
                    <DownloadIcon />
                    {downloading ? "Downloading…" : "Download"}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (editing) {
                        // Leaving edit mode keeps the edits in the preview.
                        setMarkdown(editedMarkdown);
                      }
                      setEditing((v) => !v);
                      setClausePickerOpen(false);
                    }}
                    className="text-xs font-semibold px-3 py-1 rounded-full border border-[color:var(--border)] text-[color:var(--text)] hover:bg-[color:var(--surface-strong)] transition-colors"
                  >
                    {editing ? "Done" : "Edit"}
                  </button>
                  <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-[color:var(--accent-soft)] text-[color:var(--accent-strong)]">
                    {editing ? "Editing" : "Preview"}
                  </span>
                </div>
              </div>

              {editing && clausePickerOpen && (
                <ClauseInserter
                  contractId={contractId ?? undefined}
                  onInsert={insertClauseMarkdown}
                  onClose={() => setClausePickerOpen(false)}
                />
              )}

              {/* The document scrolls inside a fixed-height panel — the page no longer grows
                  with the contract. */}
              <div className="p-6 sm:p-10 bg-[color:var(--surface-muted)] doc-preview-scroll">
                {editing ? (
                  <div className="doc-paper mx-auto w-full max-w-[880px] rounded-2xl border border-[color:var(--border)] bg-[#fcfbf5] shadow-sm p-10 sm:p-14" style={{ minHeight: "60vh" }}>
                    {parseEditBlocks(editedMarkdown).map((block) => {
                      const shared = {
                        contentEditable: true,
                        suppressContentEditableWarning: true,
                        onClick: (e: React.MouseEvent) => onEditBlockClick(e, block),
                        onContextMenu: (e: React.MouseEvent) => onEditBlockContextMenu(e, block),
                        onBlur: (e: React.FocusEvent<HTMLElement>) => commitBlockEdit(block, e.currentTarget.innerText),
                        className:
                          "outline-none rounded-md px-1 -mx-1 cursor-text hover:bg-[rgba(15,118,110,0.04)] focus:bg-[rgba(15,118,110,0.06)]",
                      };
                      if (block.kind === "h1")
                        return <h1 key={block.start} {...shared} className={`doc-heading mt-0 text-[32px] font-bold tracking-tight text-[color:var(--text)] ${shared.className}`}>{block.text}</h1>;
                      if (block.kind === "h2")
                        return <h2 key={block.start} {...shared} className={`doc-heading mt-8 text-2xl font-semibold tracking-tight text-[color:var(--text)] ${shared.className}`}>{block.text}</h2>;
                      if (block.kind === "h3")
                        return <h3 key={block.start} {...shared} className={`doc-heading mt-6 text-lg font-semibold tracking-tight text-[color:var(--text)] ${shared.className}`}>{block.text}</h3>;
                      if (block.kind === "blockquote")
                        return <blockquote key={block.start} {...shared} className={`doc-blockquote mt-6 rounded-3xl border-l-4 border-[color:var(--accent-soft)] bg-[rgba(15,118,110,0.05)] px-5 py-4 italic text-sm leading-7 ${shared.className}`}>{block.text}</blockquote>;
                      return <p key={block.start} {...shared} className={`doc-paragraph mt-4 leading-8 text-sm text-[color:var(--text)] whitespace-pre-wrap ${shared.className}`}>{block.text}</p>;
                    })}
                  </div>
                ) : (
                  <div className="doc-paper mx-auto w-full max-w-[880px] rounded-2xl border border-[color:var(--border)] bg-[#fcfbf5] shadow-sm p-10 sm:p-14" onMouseLeave={() => setHoveredClauseId(null)}>
                    {(() => {
                      const sections = parseClauseSections(markdown);
                      const preamble = markdown.slice(0, sections[0]?.start ?? markdown.length);
                      return (
                        <>
                          {/* Document title / opening text before the first "## " clause is
                              never wrapped in a clause box — no toolbar, no highlight, per
                              "do not show for non-clause text". */}
                          {preamble.trim() && (
                            <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                              {preamble}
                            </ReactMarkdown>
                          )}
                          {sections.map((section) => {
                            const hovered = hoveredClauseId === section.instanceId;
                            // Colab-style: the same action pill renders both above and below the
                            // clause card, so "Insert Above" is reachable from the top edge and
                            // "Insert Below" from the bottom edge without hunting for one toolbar.
                            const renderToolbar = (position: "top" | "bottom") => (
                              <div
                                role="toolbar"
                                aria-label={`${section.title || "Clause"} actions (${position})`}
                                className={`absolute right-3 z-10 flex items-center gap-0.5 rounded-full border border-[color:var(--border)] bg-[color:var(--surface)] shadow-md px-1 py-1 text-xs font-medium ${
                                  position === "top" ? "-top-4" : "-bottom-4"
                                }`}
                              >
                                <button
                                  type="button"
                                  title="Edit clause"
                                  className="px-2 py-1 rounded-full text-[color:var(--accent)] hover:bg-[color:var(--surface-strong)] flex items-center gap-1"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    setEditClauseTarget(section);
                                  }}
                                >
                                  <AssistantIcon />
                                  Edit
                                </button>
                                <button
                                  type="button"
                                  title="Insert clause above this one"
                                  className="px-2 py-1 rounded-full hover:bg-[color:var(--surface-strong)]"
                                  onClick={(e) => openInsertBeforeClause(e, section)}
                                >
                                  Insert Above
                                </button>
                                <button
                                  type="button"
                                  title="Insert clause below this one"
                                  className="px-2 py-1 rounded-full hover:bg-[color:var(--surface-strong)]"
                                  onClick={(e) => openInsertAfterClause(e, section)}
                                >
                                  Insert Below
                                </button>
                                <button
                                  type="button"
                                  title="Fill placeholder details"
                                  className="px-2 py-1 rounded-full hover:bg-[color:var(--surface-strong)]"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    setFillClauseTarget(section);
                                  }}
                                >
                                  Fill
                                </button>
                                <button
                                  type="button"
                                  title="Remove clause"
                                  className="px-2 py-1 rounded-full text-rose-600 hover:bg-[color:var(--surface-strong)]"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    setRemoveClauseTarget(section);
                                  }}
                                >
                                  Remove
                                </button>
                                <button
                                  type="button"
                                  title="Explain this clause"
                                  className="px-2 py-1 rounded-full hover:bg-[color:var(--surface-strong)]"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    openClauseExplain(section.title);
                                  }}
                                >
                                  ✨ Explain
                                </button>
                              </div>
                            );
                            const clauseIntel = intel?.clauses.find((c) => c.title === section.title);
                            const risk = clauseIntel?.risk;
                            return (
                              <div
                                key={section.instanceId}
                                data-clause-title={section.title}
                                style={risk ? { borderLeft: `3px solid ${riskColor(risk)}` } : undefined}
                                className={`clause-block relative rounded-2xl -mx-3 px-3 py-1 transition-colors ${
                                  hovered
                                    ? "ring-2 ring-[color:var(--accent)] bg-[rgba(15,118,110,0.04)]"
                                    : "ring-1 ring-transparent"
                                }`}
                                onMouseEnter={() => setHoveredClauseId(section.instanceId)}
                              >
                                {hovered && renderToolbar("top")}
                                <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                                  {section.markdown}
                                </ReactMarkdown>
                                <ClauseSuggestionCards clause={clauseIntel} />
                                <ClauseAnalyticsFooter clause={clauseIntel} />
                                {hovered && renderToolbar("bottom")}
                              </div>
                            );
                          })}
                        </>
                      );
                    })()}
                  </div>
                )}
              </div>
            </div>

              <ContractIntelligenceSidebar
                analysis={intel}
                loading={intelLoading}
                perspective={perspective}
                onPerspectiveChange={setPerspective}
                onScrollToClause={scrollToClauseTitle}
                onGenerateMissingClause={() => setAssistantOpen(true)}
              />
            </div>
          )}

          {/* Global Explain side panel — opened from any clause toolbar's ✨ Explain button. */}
          <ClauseExplainPopover analysis={intel} />

          {/* Generated docx Download Card — for any finalized draft. Sits right after
              Document Preview, not before it. */}
          {phase === "done" && version && version.finalized_at && (
            <div className="mt-6 max-w-6xl mx-auto">
              <div className="doc-download-card">
                <div className="doc-download-info">
                  <div className="flex flex-col">
                    <span className="doc-download-title">{docTitle}</span>
                    <span className="doc-download-format">DOCX</span>
                  </div>
                  <span className="doc-download-version">V{version.attempt}</span>
                </div>
                <button
                  onClick={download}
                  disabled={downloading}
                  className="doc-download-btn"
                  title={`Download ${docTitle}.docx`}
                >
                  {downloading ? (
                    <span className="text-xs">Preparing...</span>
                  ) : (
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
                      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                      <polyline points="7 10 12 15 17 10" />
                      <line x1="12" y1="15" x2="12" y2="3" />
                    </svg>
                  )}
                </button>
              </div>
            </div>
          )}

          {/* Right-click clause menu: Edit / Fill details / Insert above / Insert below / Remove, plus
              Insert-at-point in both Preview (block-based) and Editing (caret-based) modes.
              Clause-specific items (Edit, Fill, Remove) only render when the click landed
              inside a valid "## " clause section. */}
          {blockMenu && (
            <div
              ref={menuRef}
              role="menu"
              aria-label="Clause actions"
              className="fixed z-50 rounded-2xl border border-[color:var(--border)] bg-[color:var(--surface)] shadow-xl text-sm overflow-hidden"
              style={{ top: blockMenu.y, left: blockMenu.x }}
              onKeyDown={onBlockMenuKeyDown}
            >
              {!blockClausePickerOpen ? (
                <div className="flex min-w-[200px] flex-col">
                  {blockMenuItems.map((item, i) => (
                    <button
                      key={item.key}
                      type="button"
                      role="menuitem"
                      tabIndex={menuFocusIndex === i ? 0 : -1}
                      ref={(el) => {
                        blockMenuItemRefs.current[i] = el;
                      }}
                      className={
                        "px-4 py-2.5 text-left font-medium hover:bg-[color:var(--surface-strong)] focus:bg-[color:var(--surface-strong)] focus:outline-none " +
                        (item.destructive ? "text-rose-600" : "")
                      }
                      onClick={item.onSelect}
                      onFocus={() => setMenuFocusIndex(i)}
                    >
                      {item.label}
                    </button>
                  ))}
                  <button
                    type="button"
                    role="menuitem"
                    className="px-4 py-2.5 text-left text-[color:var(--text-muted)] hover:bg-[color:var(--surface-strong)]"
                    onClick={() => setBlockMenu(null)}
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <div className="w-[340px] max-h-[70vh] overflow-auto">
                  <ClauseInserter
                    contractId={contractId ?? undefined}
                    onInsert={insertClauseAtBlock}
                    onClose={() => {
                      setBlockMenu(null);
                      setBlockClausePickerOpen(false);
                    }}
                  />
                </div>
              )}
            </div>
          )}

          {/* A backdrop that closes the menu on any outside click, without blocking the
              document itself (clicks on the document are handled by onDocBlockClick). */}
          {blockMenu && <div className="fixed inset-0 z-40" onClick={() => { setBlockMenu(null); setBlockClausePickerOpen(false); }} />}

          {editClauseTarget && (
            <ClauseEditModal
              contractId={contractId ?? undefined}
              section={editClauseTarget}
              onSave={onSaveEditClause}
              onClose={() => setEditClauseTarget(null)}
            />
          )}

          {removeClauseTarget && (
            <ClauseRemoveConfirm
              section={removeClauseTarget}
              removing={removing}
              onConfirm={confirmRemoveClause}
              onCancel={() => setRemoveClauseTarget(null)}
            />
          )}

          {fillAllOpen && (markdown || editedMarkdown) && (
        <DocumentFillDetailsModal
          document={(editing ? editedMarkdown : markdown) ?? ""}
          knownValues={knownContractValues}
          contractId={contractId ?? undefined}
          onVariablesPersisted={onVariablesPersisted}
          onApply={(nextDocument) => {
            setMarkdown(nextDocument);
            setEditedMarkdown(nextDocument);
            setFillAllOpen(false);
            toast.success("Filled in the document's missing details.");
          }}
          onClose={() => setFillAllOpen(false)}
        />
      )}

      {fillClauseTarget && (
            <ClauseFillDetailsModal
              section={fillClauseTarget}
              knownValues={knownContractValues}
              contractId={contractId ?? undefined}
              onVariablesPersisted={onVariablesPersisted}
              onApply={onApplyFillDetails}
              onClose={() => setFillClauseTarget(null)}
            />
          )}

        </div>
      </section>

      {assistantOpen &&
        typeof document !== "undefined" &&
        createPortal(
          <div className="fixed right-0 top-0 bottom-0 w-[380px] max-w-[90vw] z-50 bg-[color:var(--surface-strong,#fff)] border-l border-[color:var(--border,rgba(0,0,0,0.08))] shadow-xl">
            <AssistantPanel
              contractId={contractId ?? undefined}
              getDocument={() => (editing ? editedMarkdown : markdown) ?? ""}
              onApplyAction={applyClauseAction}
              onClose={() => setAssistantOpen(false)}
            />
          </div>,
          document.body
        )}


      {/* Bottom chat input — sticky within the content column, so it stays aligned with the
          messages above it and does not shift when the sidebar collapses or a scrollbar
          appears. `mt-auto` keeps it at the bottom when the conversation is short. */}
      <section className="sticky bottom-6 z-40 mt-auto w-full max-w-6xl mx-auto">
        
        {/* Render selected files as chips floating above the input box */}
        {files.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-3 bg-[color:var(--surface)] p-2 rounded-2xl border border-[color:var(--border)] shadow-sm">
            {files.map((file, index) => (
              <div key={index} className="file-chip flex items-center gap-1.5 bg-[color:var(--surface-strong)] px-2.5 py-1 rounded-xl text-xs border border-[color:var(--border)]">
                <span className="file-type bg-[color:var(--accent-soft)] text-[color:var(--accent-strong)] text-[10px] font-bold px-1 rounded">
                  {file.name.split(".").pop()?.toUpperCase() ?? "FILE"}
                </span>
                <span className="truncate max-w-[120px] font-medium">{file.name}</span>
                <button 
                  type="button" 
                  onClick={() => setFiles(files.filter((_, i) => i !== index))} 
                  className="file-remove text-sm hover:text-[color:var(--accent)] font-bold transition-colors cursor-pointer"
                  disabled={isInProgress}
                >
                  ×
                </button>
              </div>
            ))}
            
            {/* Quick action to run analysis on uploaded documents */}
            {!analysis && (
              <button
                type="button"
                className="text-xs text-[color:var(--accent)] font-semibold hover:underline ml-auto mr-1 cursor-pointer"
                onClick={handleAnalyzeFiles}
                disabled={analyzingFiles || isInProgress}
              >
                {analyzingFiles ? "Analyzing..." : "Analyze clauses"}
              </button>
            )}

            {/* Mode 2: use the first upload as a template to transform in place, preserving its
                structure, rather than as reference material. */}
            <label className="basis-full flex items-center gap-2 text-xs text-[color:var(--text-muted)] mt-1 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={asTemplate}
                onChange={(event) => setAsTemplate(event.target.checked)}
                disabled={isInProgress}
              />
              <span>
                Use <b className="text-[color:var(--text)]">{files[0]?.name}</b> as a template —
                transform this document and preserve its formatting (.docx only)
              </span>
            </label>

            {/* Several independent drafts off the same source, to compare. */}
            {asTemplate && (
              <label className="basis-full flex items-center gap-2 text-xs text-[color:var(--text-muted)] cursor-pointer select-none">
                <span>Drafts to produce</span>
                <select
                  className="clause-input"
                  style={{ width: "4.5rem", padding: "0.15rem 0.35rem" }}
                  value={copies}
                  onChange={(event) => setCopies(Number(event.target.value))}
                  disabled={isInProgress}
                >
                  {[1, 2, 3, 4, 5].map((n) => (
                    <option key={n} value={n}>{n}</option>
                  ))}
                </select>
                {copies > 1 && <span>— {copies} separate contracts you can compare</span>}
              </label>
            )}
          </div>
        )}

        <div className="bottom-input-container">
          {/* Main textarea — grows with content up to a cap, then scrolls, so the input
              never jumps and never runs off the screen. */}
          <textarea
            id="request"
            rows={1}
            className="w-full resize-none border-none bg-transparent outline-none p-1 text-[color:var(--text)] text-sm overflow-y-auto"
            placeholder="Ask a question about your documents..."
            value={request}
            disabled={isInProgress}
            onChange={(event) => {
              setRequest(event.target.value);
              const el = event.currentTarget;
              el.style.height = "auto";
              el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (!busy && request.trim().length > 0) {
                  void start();
                }
              }
            }}
            style={{ minHeight: "2.5rem", maxHeight: "160px" }}
          />

          <div className="bottom-input-row border-t border-[color:var(--border)] pt-2 mt-1">
            {/* Left elements: + Documents button */}
            <div className="bottom-input-actions-left">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={isInProgress}
                className="bottom-input-btn hover:text-[color:var(--accent)]"
              >
                <PlusIcon />
                <span>Documents</span>
              </button>
              
              {/* Hidden file input */}
              <input
                type="file"
                ref={fileInputRef}
                className="sr-only"
                multiple
                accept=".pdf,.docx,.txt,.md"
                disabled={isInProgress}
                onChange={(event) => {
                  if (event.target.files) {
                    const newFiles = Array.from(event.target.files);
                    setFiles((prev) => [...prev, ...newFiles]);
                  }
                  event.target.value = "";
                }}
              />
            </div>

            {/* Right elements: Send Button */}
            <div className="bottom-input-actions-right">
              <button
                onClick={start}
                disabled={busy || request.trim().length === 0}
                className="send-btn"
                aria-label="Send to assistant"
              >
                <SendArrow />
              </button>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}