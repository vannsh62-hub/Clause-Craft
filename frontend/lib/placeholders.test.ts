import assert from "node:assert/strict";
import { test } from "node:test";
import { detectPlaceholders, applyPlaceholderValues } from "./placeholders.ts";

test("detects {{ variable }} and {{variable}} template placeholders", () => {
  const clause = "## 8. Signatures\n\n{{ party_one_signatory_name }} and {{party_two_signatory_name}}.\n";
  const fields = detectPlaceholders(clause);
  assert.deepEqual(
    fields.map((f) => f.key),
    ["party_one_signatory_name", "party_two_signatory_name"]
  );
  assert.ok(fields.every((f) => f.source === "template-variable"));
});

test("detects bracket placeholders like [INSERT ADDRESS] and [●]", () => {
  const clause = "## 9. Notices\n\nSend notices to [INSERT ADDRESS] or [●].\n";
  const fields = detectPlaceholders(clause);
  assert.equal(fields.length, 2);
  assert.ok(fields.every((f) => f.source === "bracket-placeholder"));
  assert.equal(fields[0].label, "Address");
});

test("does not treat ordinary square-bracket legal text as a placeholder", () => {
  const clause = '## 1. Definitions\n\n"Confidential Information" [as defined below] means data, [Exhibit A].\n';
  const fields = detectPlaceholders(clause);
  assert.equal(fields.length, 0);
});

test("detects an obvious empty labelled field (blank underscores after a colon)", () => {
  const clause = "## 10. Witness\n\nSignature: ___________\nDate: ................\n";
  const fields = detectPlaceholders(clause);
  const labels = fields.map((f) => f.label);
  assert.ok(labels.includes("Signature"));
  assert.ok(labels.includes("Date"));
});

test("does not treat an ordinary sentence ending in a colon as a labelled field", () => {
  const clause = "## 2. Scope\n\nThe following applies: services are rendered as described.\n";
  const fields = detectPlaceholders(clause);
  assert.equal(fields.length, 0);
});

test("deduplicates a placeholder repeated multiple times in the same clause", () => {
  const clause = "{{ effective_date }} ... later again {{ effective_date }} and once more {{effective_date}}.";
  const fields = detectPlaceholders(clause);
  assert.equal(fields.length, 1);
  assert.equal(fields[0].key, "effective_date");
});

test("prefills currentValue for template-variable fields from known contract values", () => {
  const clause = "Governed by the laws of {{ governing_law }}.";
  const fields = detectPlaceholders(clause, { governing_law: "India" });
  assert.equal(fields[0].currentValue, "India");
});

test("does not prefill currentValue for bracket placeholders even if the key happens to match a known value", () => {
  const clause = "Address: [INSERT ADDRESS]";
  const fields = detectPlaceholders(clause, { address: "221B Baker Street" });
  assert.equal(fields[0].currentValue, undefined);
});

test("returns no fields for a clause with nothing unresolved", () => {
  const clause = "## 3. Governing Law\n\nThis agreement is governed by the laws of India.\n";
  assert.deepEqual(detectPlaceholders(clause), []);
});

test("applyPlaceholderValues fills every repeated occurrence of the same placeholder", () => {
  const clause = "Effective as of {{ effective_date }}. See {{ effective_date }} above.";
  const fields = detectPlaceholders(clause);
  const { markdown, remaining } = applyPlaceholderValues(clause, fields, { effective_date: "1 June 2026" });
  assert.equal(markdown, "Effective as of 1 June 2026. See 1 June 2026 above.");
  assert.equal(remaining.length, 0);
});

test("applyPlaceholderValues never replaces unrelated text that happens to share a word", () => {
  const clause = "The [INSERT ADDRESS] is the registered address. Address changes require notice.";
  const fields = detectPlaceholders(clause);
  const { markdown } = applyPlaceholderValues(clause, fields, { address: "221B Baker Street" });
  assert.ok(markdown.includes("221B Baker Street is the registered address"));
  assert.ok(markdown.includes("Address changes require notice"));
});

test("applyPlaceholderValues leaves a field unresolved (and reports it) when given an empty value", () => {
  const clause = "{{ disclosing_party }} agrees to keep information confidential.";
  const fields = detectPlaceholders(clause);
  const { markdown, remaining } = applyPlaceholderValues(clause, fields, { disclosing_party: "" });
  assert.equal(markdown, clause);
  assert.equal(remaining.length, 1);
  assert.equal(remaining[0].key, "disclosing_party");
});

test("applyPlaceholderValues preserves markdown formatting around the replaced text", () => {
  const clause = "**Party:** {{ disclosing_party }}\n\n- {{ receiving_party }}\n";
  const fields = detectPlaceholders(clause);
  const { markdown } = applyPlaceholderValues(clause, fields, {
    disclosing_party: "ABC Pvt Ltd",
    receiving_party: "XYZ Pvt Ltd",
  });
  assert.equal(markdown, "**Party:** ABC Pvt Ltd\n\n- XYZ Pvt Ltd\n");
});

test("handles the final Signature clause with mixed placeholder kinds", () => {
  const clause = "## 8. Signatures\n\n{{ party_one_signatory_name }}\n\nTitle: ______\n\nAddress: [INSERT ADDRESS]\n";
  const fields = detectPlaceholders(clause);
  assert.equal(fields.length, 3);
  const sources = fields.map((f) => f.source).sort();
  assert.deepEqual(sources, ["bracket-placeholder", "bracket-placeholder", "template-variable"]);
});