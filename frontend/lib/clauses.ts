// Shared clause-section model for the contract Markdown document.
//
// A "clause" is a top-level section beginning with a level-two heading
// ("## 1. Title" or "## Title", numbered or not) and running up to — but not
// including — the next "## " heading, or the end of the document.
//
// This is the single source of truth for clause boundaries. Every consumer
// (context menu, edit, remove, fill-details, insertion positioning, title
// lookup, assistant-generated mutations) should go through `parseClauseSections`
// or `clauseSectionAt` rather than re-deriving boundaries with its own regex,
// so a change to what counts as a clause only has to happen in one place.
//
// Deliberately excludes the document's "# Title" (h1) and any "### " (h3)
// subheadings — only "## " starts a new clause.

export interface ClauseSection {
  /** Stable-for-this-parse id; not persisted across re-parses/edits. Safe to
   *  use as a React key or to correlate a menu action with the section it was
   *  opened for, but do not stash it for longer than one render cycle since
   *  editing the document shifts offsets and re-parsing reassigns ids. */
  instanceId: string;
  /** Clause title with any leading "N. " number and the "## " marker stripped. */
  title: string;
  /** The full heading line, including the "## " marker and any number. */
  heading: string;
  /** Offset of the section's first character (the "#" of "## ") in `doc`. */
  start: number;
  /** Offset one past the section's last character (start of the next "## ",
   *  or `doc.length`). */
  end: number;
  /** doc.slice(start, end) — the complete clause section, heading + body. */
  markdown: string;
}

const HEADING_RE = /^##[ \t]+(.*)$/gm;
const HEADING_NUMBER_RE = /^\d+\.[ \t]+/;

/** Parse every "## " clause section out of `doc`, in document order. */
export function parseClauseSections(doc: string): ClauseSection[] {
  if (!doc) return [];

  const starts: { index: number; headingLine: string; titleRaw: string }[] = [];
  HEADING_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = HEADING_RE.exec(doc))) {
    // Find the actual end-of-line for the heading text (regex `.` above already
    // stops at the newline since it's not in dotAll mode).
    const lineEnd = doc.indexOf("\n", m.index);
    const headingLine = lineEnd === -1 ? doc.slice(m.index) : doc.slice(m.index, lineEnd);
    starts.push({ index: m.index, headingLine, titleRaw: m[1] });
  }

  const sections: ClauseSection[] = [];
  for (let i = 0; i < starts.length; i++) {
    const cur = starts[i];
    const next = starts[i + 1];
    const start = cur.index;
    const end = next ? next.index : doc.length;
    const title = cur.titleRaw.replace(HEADING_NUMBER_RE, "").trim();
    sections.push({
      instanceId: `clause-${i}-${start}`,
      title,
      heading: cur.headingLine,
      start,
      end,
      markdown: doc.slice(start, end),
    });
  }
  return sections;
}

/** Find the clause section (if any) that contains `offset`. Mirrors the
 *  previous `clauseSectionAt` behaviour: an offset before the first "## "
 *  heading (e.g. in the document's opening paragraph) returns null. */
export function clauseSectionAt(doc: string, offset: number): ClauseSection | null {
  const sections = parseClauseSections(doc);
  for (const s of sections) {
    if (offset >= s.start && offset < s.end) return s;
  }
  // Offset exactly at doc.length (click landed on the very last char) should
  // still resolve to the last section, matching the inclusive-end behaviour
  // insertion/removal relied on previously.
  if (sections.length > 0 && offset >= sections[sections.length - 1].end) {
    const last = sections[sections.length - 1];
    if (offset === doc.length) return last;
  }
  return null;
}

/** Strips a leading "N. " / "N) " clause number, if present, so a numbered title
 *  (as an assistant or user might type/return one, e.g. "4. Service Credits") compares
 *  equal to the unnumbered title `parseClauseSections` stores. */
function stripLeadingNumber(title: string): string {
  return title.trim().replace(/^\d+[.)]\s*/, "");
}

/** Find a clause section by title (case-insensitive, trimmed, and tolerant of an optional
 *  leading clause number). Returns null if no clause matches, and the special value
 *  `"ambiguous"` if more than one clause shares the same title — callers (e.g. assistant
 *  mutations) should turn that into a clarification question rather than guessing. */
export function findClauseByTitle(
  doc: string,
  title: string
): ClauseSection | "ambiguous" | null {
  const needle = stripLeadingNumber(title).toLowerCase();
  const matches = parseClauseSections(doc).filter(
    (s) => stripLeadingNumber(s.title).toLowerCase() === needle
  );
  if (matches.length === 0) return null;
  if (matches.length > 1) return "ambiguous";
  return matches[0];
}

/** Replace one clause section's markdown in `doc`, by instanceId, with
 *  `replacement` (which itself should be a complete "## ..." section, or ""
 *  to delete it — callers wanting deletion should prefer `removeClauseSection`
 *  for its whitespace cleanup). Returns `doc` unchanged if the instanceId no
 *  longer matches (e.g. stale reference after a concurrent edit). */
export function replaceClauseSection(doc: string, instanceId: string, replacement: string): string {
  const sections = parseClauseSections(doc);
  const section = sections.find((s) => s.instanceId === instanceId);
  if (!section) return doc;
  return doc.slice(0, section.start) + replacement + doc.slice(section.end);
}

/** Remove one clause section's markdown from `doc` by instanceId, collapsing
 *  any resulting run of 3+ blank lines down to a single blank line. */
export function removeClauseSection(doc: string, instanceId: string): string {
  const sections = parseClauseSections(doc);
  const section = sections.find((s) => s.instanceId === instanceId);
  if (!section) return doc;
  const spliced = doc.slice(0, section.start) + doc.slice(section.end);
  return spliced.replace(/\n{3,}/g, "\n\n");
}

/** Renumber every "## " heading in document order starting at 1. Clause headings carry
 *  their number as literal text ("## 1. Scope of Services"), not auto-numbering, so
 *  nothing renumbers them on its own once a clause is inserted, removed, or reordered.
 *  Matches every "## ..." heading whether or not it already has a leading number — a
 *  freshly-inserted library clause comes in as plain "## Confidentiality". */
export function renumberClauseHeadings(doc: string): string {
  const headingRe = /^(##[ \t]+)(?:\d+\.[ \t]+)?(.*)$/gm;
  let n = 0;
  return doc.replace(headingRe, (_match, prefix: string, title: string) => {
    n += 1;
    return `${prefix}${n}. ${title}`;
  });
}

/** Insert `snippet` (a complete "## ..." section) after the clause titled
 *  `afterTitle`, or at the end of the document if `afterTitle` is null/empty.
 *  Renumbers headings afterward. Returns `doc` unchanged (renumbering aside)
 *  if `afterTitle` is given but matches no clause or matches more than one —
 *  callers should check `findClauseByTitle` first if they need to distinguish
 *  "not found" from "inserted", e.g. to surface an "ambiguous" result to the
 *  user rather than silently doing nothing. */
export function insertClauseSection(
  doc: string,
  snippet: string,
  afterTitle?: string | null
): string {
  let insertAt = doc.length;
  if (afterTitle) {
    const target = findClauseByTitle(doc, afterTitle);
    if (target === "ambiguous" || target === null) return doc;
    insertAt = target.end;
  }
  const spliced = doc.slice(0, insertAt) + snippet + doc.slice(insertAt);
  return renumberClauseHeadings(spliced);
}