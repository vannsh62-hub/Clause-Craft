import assert from "node:assert/strict";
import { test } from "node:test";
import { fieldStatus } from "./fieldStatus.ts";

test("existing variables report known", () => {
  assert.equal(fieldStatus(true, true), "known");
});

test("newly entered variables report new", () => {
  assert.equal(fieldStatus(true, false), "new");
});

test("missing variables report required", () => {
  assert.equal(fieldStatus(false, false), "required");
  assert.equal(fieldStatus(false, true), "required");
});
