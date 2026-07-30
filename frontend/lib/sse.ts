import type { RunEvent, RunEventType } from "./types";

/**
 * Server-sent events use CRLF. `sse-starlette` sends `\r\n` between fields and `\r\n\r\n`
 * between frames, as the spec allows.
 *
 * Getting this wrong is silent and total: `Number("1\r")` is `NaN`, so every frame is
 * discarded, the client never advances its `seq`, and it reconnects at `seq=0` forever. The
 * server logs a healthy 200 each time. Nothing looks broken except that nothing happens.
 */
const FRAME_SEPARATOR = /\r?\n\r?\n/;

/** Split a buffer into complete frames, returning the incomplete tail for the next read. */
export function splitFrames(buffer: string): { frames: string[]; rest: string } {
  const parts = buffer.split(FRAME_SEPARATOR);
  const rest = parts.pop() ?? "";
  return { frames: parts, rest };
}

export function parseFrame(frame: string): RunEvent | null {
  let seq = 0;
  let name = "";
  let data = "";

  for (const raw of frame.split(/\r?\n/)) {
    const line = raw.trimEnd();
    if (line.startsWith("id:")) seq = Number(line.slice(3).trim());
    else if (line.startsWith("event:")) name = line.slice(6).trim();
    else if (line.startsWith("data:")) data = line.slice(5).trim();
  }

  if (!name || !Number.isInteger(seq) || seq <= 0) return null;

  try {
    return { seq, event: name as RunEventType, data: JSON.parse(data) };
  } catch {
    return null;
  }
}
