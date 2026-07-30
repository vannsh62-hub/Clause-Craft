// Deterministic placeholder detection for one clause's Markdown.
//
// This is intentionally NOT LLM-based: template variables and bracket placeholders follow a
// small, fixed set of syntactic shapes, so a regex scan is both cheaper and more reliable than
// asking a model to spot them — and it can't hallucinate a placeholder that isn't there or miss
// one that is. The LLM (added in a later stage, via the backend /clauses/analyse endpoint) is
// only used for the *semantic* question "does this clause seem to be missing something a human
// would notice," on top of what this parser already found deterministically.

export interface ClauseMissingField {
  key: string;
  label: string;
  placeholder: string;
  currentValue?: string;
  source: "template-variable" | "bracket-placeholder" | "agent";
}

interface RawMatch {
  key: string;
  label: string;
  placeholder: string;
  source: ClauseMissingField["source"];
  start: number;
  end: number;
}

// {{ variable_name }} or {{variable_name}}. Variable names follow the clause-template
// vocabulary: snake_case identifiers, same as backend.invariants.render's substitution.
const TEMPLATE_VAR_RE = /\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g;

// Explicit bracket placeholders only: "[INSERT ...]", "[TBD]", "[FILL IN ...]", "[●]", "[•]".
// Deliberately does NOT match arbitrary square-bracket text ("[as defined below]",
// "[Exhibit A]") — only brackets that open with one of these explicit placeholder markers.
const BRACKET_RE = /\[\s*(INSERT\b[^\]]*|TBD|FILL[ _-]?IN\b[^\]]*|●|•)\s*\]/gi;

// A labelled blank: a line reading "Label: ____" / "Label: ...." / "Label: []" — a common
// paper-form style of leaving a field for someone to fill in by hand. Requires an explicit
// blank marker (3+ underscores, 4+ dots, or empty brackets) right after the colon, so ordinary
// sentences ending in a colon are never mistaken for a field.
const LABELLED_BLANK_RE = /^([A-Za-z][A-Za-z0-9 /()'-]{1,60}):[ \t]*(_{3,}|\.{4,}|\[\s*\])[ \t]*$/gm;

function slugify(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function humanize(slug: string): string {
  return slug
    .split("_")
    .filter(Boolean)
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ");
}

function collectRawMatches(clauseMarkdown: string): RawMatch[] {
  const matches: RawMatch[] = [];

  TEMPLATE_VAR_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = TEMPLATE_VAR_RE.exec(clauseMarkdown))) {
    const key = m[1];
    matches.push({
      key,
      label: humanize(key),
      placeholder: m[0],
      source: "template-variable",
      start: m.index,
      end: m.index + m[0].length,
    });
  }

  BRACKET_RE.lastIndex = 0;
  while ((m = BRACKET_RE.exec(clauseMarkdown))) {
    const inner = m[1].trim();
    // Strip a leading "INSERT " / "FILL IN " marker for the label/key, so "[INSERT ADDRESS]"
    // reads as "Address" rather than "Insert Address".
    const stripped = inner.replace(/^(INSERT|FILL[ _-]?IN)\b\s*/i, "").trim();
    const base = stripped || inner || "field";
    const key = slugify(base) || `field_${m.index}`;
    matches.push({
      key,
      label: humanize(key) || "Field",
      placeholder: m[0],
      source: "bracket-placeholder",
      start: m.index,
      end: m.index + m[0].length,
    });
  }

  LABELLED_BLANK_RE.lastIndex = 0;
  while ((m = LABELLED_BLANK_RE.exec(clauseMarkdown))) {
    const label = m[1].trim();
    const key = slugify(label);
    matches.push({
      key,
      label,
      placeholder: m[2],
      source: "bracket-placeholder",
      start: m.index + m[0].indexOf(m[2], m[1].length),
      end: m.index + m[0].length,
    });
  }

  matches.sort((a, b) => a.start - b.start);
  return matches;
}

/** Detect every unresolved field in one clause's Markdown. `knownValues` is the current
 *  contract's already-resolved variables (e.g. from `resolve_variables` on the backend) keyed
 *  by template-variable name — used to prefill `currentValue` for `template-variable` fields so
 *  the user isn't asked to retype something already known. Fields are deduplicated by key (a
 *  placeholder repeated three times in one clause produces one field, not three) so filling it
 *  once fills every occurrence. */
export function detectPlaceholders(
  clauseMarkdown: string,
  knownValues: Record<string, string> = {}
): ClauseMissingField[] {
  const raw = collectRawMatches(clauseMarkdown);
  const byKey = new Map<string, ClauseMissingField>();
  for (const r of raw) {
    if (byKey.has(r.key)) continue;
    const currentValue = r.source === "template-variable" ? knownValues[r.key] : undefined;
    byKey.set(r.key, {
      key: r.key,
      label: r.label,
      placeholder: r.placeholder,
      ...(currentValue ? { currentValue } : {}),
      source: r.source,
    });
  }
  return Array.from(byKey.values());
}

function escapeForRegex(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Apply supplied values to a clause's Markdown, replacing every occurrence of each field's
 *  exact placeholder token (never a loose word match, so unrelated prose that happens to share
 *  a word is left untouched) with the given value. Never overwrites a field with an empty
 *  value — a blank entry in `values` is treated as "leave this placeholder as-is", not as "clear
 *  the known value". Returns the updated markdown and which of `fields` are still unresolved. */
/** Auto-resolve every placeholder in `clauseMarkdown` whose key already exists in
 *  `knownValues` (Variable Memory), leaving unknown placeholders untouched for a later Fill
 *  Details pass. Used to pre-fill inserted/AI-generated/AI-edited clauses before they ever
 *  reach the document, so only genuinely new fields prompt the user. */
export function resolveKnownPlaceholders(
  clauseMarkdown: string,
  knownValues: Record<string, string>
): string {
  if (!knownValues || Object.keys(knownValues).length === 0) return clauseMarkdown;
  const fields = detectPlaceholders(clauseMarkdown, knownValues).filter((f) => f.currentValue);
  if (fields.length === 0) return clauseMarkdown;
  const values = Object.fromEntries(fields.map((f) => [f.key, f.currentValue as string]));
  return applyPlaceholderValues(clauseMarkdown, fields, values).markdown;
}

export function applyPlaceholderValues(
  clauseMarkdown: string,
  fields: ClauseMissingField[],
  values: Record<string, string>
): { markdown: string; remaining: ClauseMissingField[] } {
  let next = clauseMarkdown;
  const remaining: ClauseMissingField[] = [];
  for (const field of fields) {
    const value = values[field.key];
    if (value == null || value.trim() === "") {
      remaining.push(field);
      continue;
    }
    const re = new RegExp(escapeForRegex(field.placeholder), "g");
    next = next.replace(re, value);
  }
  return { markdown: next, remaining };
}