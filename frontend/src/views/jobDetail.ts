// Job detail: metadata, a cancel action, and a live-tailing log view.
//
// The log pane consumes the SSE stream; new lines append as they arrive and the
// stream closes itself when the job reaches a terminal state. The returned
// teardown aborts the stream when the user navigates away mid-run.

import { getJob, cancelJob, ApiError } from "../api.ts";
import { streamLogs } from "../sse.ts";
import { el, clear, statusBadge, formatTime } from "../dom.ts";
import { isTerminal } from "../types.ts";
import type { Teardown } from "../router.ts";
import type { Job } from "../types.ts";

export async function renderJobDetail(
  root: HTMLElement,
  params: Record<string, string>,
): Promise<Teardown> {
  const jobId = params.id ?? "";
  clear(root);
  root.append(el("h1", {}, [`Job ${jobId}`]));
  const meta = el("div", { class: "meta" }, ["Loading…"]);
  root.append(meta);

  const controller = new AbortController();

  let job: Job;
  try {
    job = await getJob(jobId);
  } catch (error) {
    clear(meta);
    meta.className = "error";
    meta.textContent = describe(error);
    return () => controller.abort();
  }

  renderMeta(meta, job, root);

  const logPane = el("pre", { class: "logs" }, []);
  root.append(el("h2", {}, ["Logs"]));
  root.append(logPane);
  const statusLine = el("p", { class: "muted" }, [
    isTerminal(job.status) ? "Replaying logs…" : "Streaming live…",
  ]);
  root.append(statusLine);

  void streamLogs(
    jobId,
    {
      onLine: (line) => appendLine(logPane, line),
      onEnd: (status) => {
        statusLine.textContent = status
          ? `Stream ended — job ${status}.`
          : "Stream ended.";
      },
      onError: (error) => {
        statusLine.className = "error";
        statusLine.textContent = describe(error);
      },
    },
    controller.signal,
  );

  return () => controller.abort();
}

function renderMeta(meta: HTMLElement, job: Job, root: HTMLElement): void {
  clear(meta);
  meta.append(
    field("Pipeline", el("a", { href: `/pipelines/${job.pipeline_id}`, "data-link": "" }, [job.pipeline_id])),
    field("Status", statusBadge(job.status)),
    field("Trigger", document.createTextNode(`${job.trigger_type} ${job.trigger_ref ?? ""}`.trim())),
    field("Created", document.createTextNode(formatTime(job.created_at))),
    field("Started", document.createTextNode(formatTime(job.started_at))),
    field("Finished", document.createTextNode(formatTime(job.finished_at))),
  );
  if (job.failure_reason) {
    meta.append(field("Failure", document.createTextNode(job.failure_reason)));
  }
  if (!isTerminal(job.status)) {
    meta.append(cancelControl(job, root));
  }
}

function field(label: string, value: Node): HTMLElement {
  return el("div", { class: "field" }, [
    el("span", { class: "label" }, [label]),
    value,
  ]);
}

function cancelControl(job: Job, root: HTMLElement): HTMLElement {
  const button = el("button", { class: "btn btn-danger", type: "button" }, [
    "Cancel",
  ]);
  button.addEventListener("click", () => {
    void doCancel(job.id, button, root);
  });
  return field("Actions", button);
}

async function doCancel(
  jobId: string,
  button: HTMLButtonElement,
  root: HTMLElement,
): Promise<void> {
  button.disabled = true;
  button.textContent = "Cancelling…";
  try {
    const updated = await cancelJob(jobId);
    button.replaceWith(statusBadge(updated.status));
  } catch (error) {
    button.disabled = false;
    button.textContent = "Cancel";
    const banner = el("p", { class: "error" }, [describe(error)]);
    root.prepend(banner);
    setTimeout(() => banner.remove(), 5000);
  }
}

function appendLine(pane: HTMLElement, line: string): void {
  const atBottom =
    pane.scrollTop + pane.clientHeight >= pane.scrollHeight - 4;
  pane.append(document.createTextNode(line + "\n"));
  if (atBottom) pane.scrollTop = pane.scrollHeight;
}

function describe(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "Unauthorized — set a token.";
    if (error.status === 403) return "Forbidden — you lack permission.";
    if (error.status === 404) return "Job not found.";
    if (error.status === 409) return "Job is not running; cannot cancel.";
    return `Error ${error.status}: ${error.message}`;
  }
  return error instanceof Error ? error.message : String(error);
}
