import assert from "node:assert/strict";
import { test } from "node:test";
import { parseFrame, splitFrames } from "./sse.ts";

// Exactly what `sse-starlette` puts on the wire.
const CRLF_FRAME = 'id: 1\r\nevent: stage\r\ndata: {"stage": "planning", "status": "started"}';

test("parses a CRLF frame, which is what the server actually sends", () => {
  const event = parseFrame(CRLF_FRAME);

  assert.ok(event, "a CRLF frame must parse; Number('1\\r') is NaN and drops it silently");
  assert.equal(event.seq, 1);
  assert.equal(event.event, "stage");
  assert.deepEqual(event.data, { stage: "planning", status: "started" });
});

test("parses an LF-only frame too", () => {
  const event = parseFrame('id: 7\nevent: complete\ndata: {"score": 98}');
  assert.equal(event?.seq, 7);
  assert.equal(event?.event, "complete");
});

test("splits on CRLF frame boundaries and keeps the incomplete tail", () => {
  const { frames, rest } = splitFrames(`${CRLF_FRAME}\r\n\r\nid: 2\r\nevent: tool_call\r\ndata: {}`);

  assert.equal(frames.length, 1);
  assert.equal(parseFrame(frames[0])?.seq, 1);
  assert.equal(parseFrame(rest)?.seq, 2, "the tail is a whole frame once the next read lands");
});

test("a partial frame is not parsed as a whole one", () => {
  const { frames, rest } = splitFrames("id: 1\r\nevent: sta");
  assert.deepEqual(frames, []);
  assert.equal(rest, "id: 1\r\nevent: sta");
});

test("rejects frames with no seq, so the client never rewinds to zero", () => {
  assert.equal(parseFrame("event: stage\r\ndata: {}"), null);
  assert.equal(parseFrame("id: 0\r\nevent: stage\r\ndata: {}"), null);
});

test("rejects a frame with no event name", () => {
  assert.equal(parseFrame("id: 3\r\ndata: {}"), null);
});

test("rejects malformed JSON rather than throwing mid-stream", () => {
  assert.equal(parseFrame("id: 3\r\nevent: stage\r\ndata: {not json}"), null);
});

test("data containing a colon survives", () => {
  const event = parseFrame('id: 4\r\nevent: tool_result\r\ndata: {"output": "wrote: draft_v1.md"}');
  assert.deepEqual(event?.data, { output: "wrote: draft_v1.md" });
});
