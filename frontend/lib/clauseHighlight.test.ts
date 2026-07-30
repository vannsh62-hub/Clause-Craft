import assert from "node:assert/strict";
import { test } from "node:test";
import { clauseTitleFrom, selectChangedTitles } from "./clauseHighlight.ts";

test("clauseTitleFrom extracts the clause title, stripping any leading number", () => {
  const md = "## 3. Payment Terms\n\nPayment is due within {{ payment_days }} days.\n";
  assert.equal(clauseTitleFrom(md), "Payment Terms");
});

test("clauseTitleFrom returns undefined for text with no clause heading", () => {
  assert.equal(clauseTitleFrom("Just a preamble paragraph, no heading."), undefined);
});

test("selectChangedTitles includes only candidates whose content actually changed", () => {
  const result = selectChangedTitles([
    { title: "Payment Terms", before: "{{ payment_days }}", after: "30" },
    { title: "Confidentiality", before: "already resolved", after: "already resolved" },
  ]);
  assert.deepEqual(result, ["Payment Terms"]);
});

test("selectChangedTitles returns an empty array when nothing changed", () => {
  const result = selectChangedTitles([{ title: "Notices", before: "same", after: "same" }]);
  assert.deepEqual(result, []);
});
