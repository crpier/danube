// Server-Sent Events reader for the job log stream.
//
// The Master streams logs at `GET /api/v1/jobs/{id}/logs/stream` as
// `text/event-stream`: a `data: <line>` frame per log line, terminated by an
// `event: end` frame carrying `{"status": ...}`. The browser `EventSource` API
// would be the natural fit but cannot attach an `Authorization` header, so this
// reads the stream over `fetch` and parses the SSE frames by hand. Pass an
// `AbortSignal` to stop streaming (e.g. when navigating away).

import { authHeaders } from "./auth.ts";
import { ApiError } from "./api.ts";

export interface LogStreamHandlers {
  onLine: (line: string) => void;
  onEnd?: (status: string | null) => void;
  onError?: (error: unknown) => void;
}

export async function streamLogs(
  jobId: string,
  handlers: LogStreamHandlers,
  signal: AbortSignal,
): Promise<void> {
  const path = `/api/v1/jobs/${encodeURIComponent(jobId)}/logs/stream`;
  let response: Response;
  try {
    response = await fetch(path, { headers: authHeaders(), signal });
  } catch (error) {
    if (!signal.aborted) handlers.onError?.(error);
    return;
  }
  if (!response.ok) {
    handlers.onError?.(new ApiError(response.status, response.statusText));
    return;
  }
  const body = response.body;
  if (body === null) {
    handlers.onEnd?.(null);
    return;
  }

  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // SSE events are separated by a blank line.
      let separator = buffer.indexOf("\n\n");
      while (separator !== -1) {
        const frame = buffer.slice(0, separator);
        buffer = buffer.slice(separator + 2);
        if (dispatchFrame(frame, handlers)) return;
        separator = buffer.indexOf("\n\n");
      }
    }
  } catch (error) {
    if (!signal.aborted) handlers.onError?.(error);
  }
}

// Parse one SSE frame; returns true when it was the terminal `end` event.
function dispatchFrame(frame: string, handlers: LogStreamHandlers): boolean {
  let event = "message";
  const dataLines: string[] = [];
  for (const raw of frame.split("\n")) {
    if (raw.startsWith("event:")) {
      event = raw.slice("event:".length).trim();
    } else if (raw.startsWith("data:")) {
      dataLines.push(raw.slice("data:".length).replace(/^ /, ""));
    }
  }
  const data = dataLines.join("\n");
  if (event === "end") {
    handlers.onEnd?.(parseStatus(data));
    return true;
  }
  handlers.onLine(data);
  return false;
}

function parseStatus(data: string): string | null {
  try {
    const parsed = JSON.parse(data) as { status?: unknown };
    return typeof parsed.status === "string" ? parsed.status : null;
  } catch {
    return null;
  }
}
