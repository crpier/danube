// Job history: most-recent-first list of jobs with status, linking to detail.

import { listJobs, ApiError } from "../api.ts";
import { el, clear, statusBadge, formatTime } from "../dom.ts";
import type { Job } from "../types.ts";

export async function renderJobs(root: HTMLElement): Promise<void> {
  clear(root);
  root.append(el("h1", {}, ["Jobs"]));
  const status = el("p", { class: "muted" }, ["Loading jobs…"]);
  root.append(status);
  try {
    const page = await listJobs();
    status.remove();
    root.append(el("p", { class: "muted" }, [`${page.total} job(s) total`]));
    if (page.items.length === 0) {
      root.append(el("p", { class: "muted" }, ["No jobs yet."]));
      return;
    }
    root.append(table(page.items));
  } catch (error) {
    status.className = "error";
    status.textContent = describe(error);
  }
}

function table(jobs: Job[]): HTMLElement {
  const rows = jobs.map((job) =>
    el("tr", {}, [
      el("td", {}, [
        el("a", { href: `/jobs/${job.id}`, "data-link": "" }, [job.id]),
      ]),
      el("td", {}, [job.pipeline_id]),
      el("td", {}, [statusBadge(job.status)]),
      el("td", {}, [job.trigger_type]),
      el("td", {}, [formatTime(job.created_at)]),
    ]),
  );
  return el("table", { class: "grid" }, [
    el("thead", {}, [
      el("tr", {}, [
        el("th", {}, ["Job"]),
        el("th", {}, ["Pipeline"]),
        el("th", {}, ["Status"]),
        el("th", {}, ["Trigger"]),
        el("th", {}, ["Created"]),
      ]),
    ]),
    el("tbody", {}, rows),
  ]);
}

function describe(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "Unauthorized — set a token to view jobs.";
    return `Error ${error.status}: ${error.message}`;
  }
  return error instanceof Error ? error.message : String(error);
}
