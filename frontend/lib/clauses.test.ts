import assert from "node:assert/strict";
import { test } from "node:test";
import {
  parseClauseSections,
  clauseSectionAt,
  findClauseByTitle,
  replaceClauseSection,
  removeClauseSection,
  insertClauseSection,
  renumberClauseHeadings,
} from "./clauses.ts";

const DOC = `# Non-Disclosure Agreement

Intro paragraph before any clause.

## 1. Scope of Services

The vendor shall provide services as described in Exhibit A.

### Sub-detail

Some detail text.

## 2. Confidentiality

Both parties agree to keep information confidential.

## 3. Signatures

{{ party_one_signatory_name }}
`;

test("parses every ## clause section in document order", () => {
  const sections = parseClauseSections(DOC);
  assert.equal(sections.length, 3);
  assert.deepEqual(
    sections.map((s) => s.title),
    ["Scope of Services", "Confidentiality", "Signatures"]
  );
});

test("does not treat the h1 title or ### subheadings as separate clauses", () => {
  const sections = parseClauseSections(DOC);
  assert.ok(!sections.some((s) => s.title.includes("Non-Disclosure Agreement")));
  // The "### Sub-detail" subheading must remain inside clause 1's body, not
  // spawn its own section.
  assert.ok(sections[0].markdown.includes("### Sub-detail"));
});

test("supports unnumbered headings", () => {
  const doc = `## Governing Law\n\nThis agreement is governed by the laws of India.\n`;
  const sections = parseClauseSections(doc);
  assert.equal(sections.length, 1);
  assert.equal(sections[0].title, "Governing Law");
});

test("handles a clause at the end of the document with no trailing heading", () => {
  const sections = parseClauseSections(DOC);
  const last = sections[sections.length - 1];
  assert.equal(last.end, DOC.length);
  assert.ok(last.markdown.trim().endsWith("{{ party_one_signatory_name }}"));
});

test("handles the final Signature clause specifically", () => {
  const sections = parseClauseSections(DOC);
  const sig = sections.find((s) => s.title === "Signatures");
  assert.ok(sig);
  assert.ok(sig!.markdown.includes("{{ party_one_signatory_name }}"));
});

test("clauseSectionAt selects the correct clause from an offset inside its body", () => {
  const idx = DOC.indexOf("Both parties agree");
  const section = clauseSectionAt(DOC, idx);
  assert.ok(section);
  assert.equal(section!.title, "Confidentiality");
});

test("clauseSectionAt returns null for an offset before any clause heading", () => {
  const idx = DOC.indexOf("Intro paragraph");
  assert.equal(clauseSectionAt(DOC, idx), null);
});

test("clauseSectionAt resolves an offset inside a ### subheading to its parent clause", () => {
  const idx = DOC.indexOf("Some detail text");
  const section = clauseSectionAt(DOC, idx);
  assert.equal(section?.title, "Scope of Services");
});

test("findClauseByTitle is case-insensitive and trims whitespace", () => {
  const section = findClauseByTitle(DOC, "  confidentiality  ");
  assert.ok(section && section !== "ambiguous");
  assert.equal((section as any).title, "Confidentiality");
});

test("findClauseByTitle returns null when no clause matches", () => {
  assert.equal(findClauseByTitle(DOC, "Arbitration"), null);
});

test("findClauseByTitle reports ambiguity when multiple clauses share a title", () => {
  const doc = `## Termination\n\nBody one.\n\n## Termination\n\nBody two.\n`;
  assert.equal(findClauseByTitle(doc, "Termination"), "ambiguous");
});

test("replaceClauseSection replaces only the targeted clause, preserving the rest exactly", () => {
  const sections = parseClauseSections(DOC);
  const target = sections.find((s) => s.title === "Confidentiality")!;
  const next = replaceClauseSection(DOC, target.instanceId, "## 2. Confidentiality\n\nRewritten body.\n\n");
  assert.ok(next.includes("Rewritten body."));
  assert.ok(next.includes("## 1. Scope of Services"));
  assert.ok(next.includes("## 3. Signatures"));
  assert.ok(!next.includes("Both parties agree to keep information confidential."));
});

test("removeClauseSection removes the complete section and collapses extra blank lines", () => {
  const sections = parseClauseSections(DOC);
  const target = sections.find((s) => s.title === "Scope of Services")!;
  const next = removeClauseSection(DOC, target.instanceId);
  assert.ok(!next.includes("## 1. Scope of Services"));
  assert.ok(!next.includes("Exhibit A"));
  assert.ok(!/\n{3,}/.test(next));
  // Untouched clauses remain byte-for-byte.
  assert.ok(next.includes("## 2. Confidentiality"));
  assert.ok(next.includes("## 3. Signatures"));
});

test("preserves unrelated square-bracket legal text as not a clause boundary concern", () => {
  const doc = `## 1. Definitions\n\n"Confidential Information" [as defined below] means any data.\n`;
  const sections = parseClauseSections(doc);
  assert.equal(sections.length, 1);
  assert.ok(sections[0].markdown.includes("[as defined below]"));
});

test("renumberClauseHeadings numbers every ## heading in document order", () => {
  const doc = "## Scope\n\nbody\n\n## 7. Confidentiality\n\nbody\n\n## Signatures\n\nbody\n";
  const next = renumberClauseHeadings(doc);
  assert.ok(next.includes("## 1. Scope"));
  assert.ok(next.includes("## 2. Confidentiality"));
  assert.ok(next.includes("## 3. Signatures"));
});

test("insertClauseSection inserts after the named clause and renumbers", () => {
  const next = insertClauseSection(DOC, "\n\n## Governing Law\n\nGoverned by IN law.\n", "Confidentiality");
  const sections = parseClauseSections(next);
  assert.deepEqual(
    sections.map((s) => s.title),
    ["Scope of Services", "Confidentiality", "Governing Law", "Signatures"]
  );
  assert.ok(next.includes("## 3. Governing Law"));
  assert.ok(next.includes("## 4. Signatures"));
});

test("insertClauseSection appends at the end when no title is given", () => {
  const next = insertClauseSection(DOC, "\n\n## Governing Law\n\nGoverned by IN law.\n", null);
  const sections = parseClauseSections(next);
  assert.equal(sections[sections.length - 1].title, "Governing Law");
});

test("insertClauseSection leaves the document unchanged for an unknown target title", () => {
  const next = insertClauseSection(DOC, "\n\n## Governing Law\n\nbody\n", "Nonexistent Clause");
  assert.equal(next, DOC);
});

test("insertClauseSection leaves the document unchanged for an ambiguous target title", () => {
  const doc = "## Confidentiality\n\nA.\n\n## Confidentiality\n\nB.\n";
  const next = insertClauseSection(doc, "\n\n## New\n\nbody\n", "Confidentiality");
  assert.equal(next, doc);
});
