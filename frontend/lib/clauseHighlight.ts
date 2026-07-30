import { parseClauseSections } from "./clauses.ts";

/** The title of the first (only, in practice) clause section found in `markdown` — used to
 *  key the "recently changed" highlight so it survives clause renumbering (which rewrites the
 *  "N. " prefix but never the title text itself). */
export function clauseTitleFrom(markdown: string): string | undefined {
  return parseClauseSections(markdown)[0]?.title;
}

/** Filters a set of before/after clause candidates down to only those whose content actually
 *  changed — so a placeholder that already held the correct value (before === after) is never
 *  highlighted, per "if nothing changed, don't highlight it". */
export function selectChangedTitles(
  candidates: { title: string; before: string; after: string }[]
): string[] {
  return candidates.filter((c) => c.before !== c.after).map((c) => c.title);
}
