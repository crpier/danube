// Pipeline list: every pipeline with a manual "Run" action that triggers a job
// and navigates to its detail page.

import { listPipelines, runPipeline, ApiError } from "../api.ts";
import { el, clear } from "../dom.ts";
import { navigate } from "../router.ts";
import type { Pipeline } from "../types.ts";

export async function renderPipelines(root: HTMLElement): Promise<void> {
  clear(root);
  root.append(el("h1", {}, ["Pipelines"]));
  const status = el("p", { class: "muted" }, ["Loading pipelines…"]);
  root.append(status);
  try {
    const page = await listPipelines();
    status.remove();
    if (page.items.length === 0) {
      root.append(el("p", { class: "muted" }, ["No pipelines configured."]));
      return;
    }
    root.append(table(page.items, root));
  } catch (error) {
    status.className = "error";
    status.textContent = describe(error);
  }
}

function table(pipelines: Pipeline[], root: HTMLElement): HTMLElement {
  const rows = pipelines.map((pipeline) =>
    el("tr", {}, [
      el("td", {}, [
        el("a", { href: `/pipelines/${pipeline.id}`, "data-link": "" }, [
          pipeline.name,
        ]),
      ]),
      el("td", {}, [pipeline.repo_url]),
      el("td", {}, [pipeline.worker_image]),
      el("td", {}, [runButton(pipeline, root)]),
    ]),
  );
  return el("table", { class: "grid" }, [
    el("thead", {}, [
      el("tr", {}, [
        el("th", {}, ["Name"]),
        el("th", {}, ["Repository"]),
        el("th", {}, ["Worker image"]),
        el("th", {}, ["Actions"]),
      ]),
    ]),
    el("tbody", {}, rows),
  ]);
}

function runButton(pipeline: Pipeline, root: HTMLElement): HTMLElement {
  const button = el("button", { class: "btn", type: "button" }, ["Run"]);
  button.addEventListener("click", () => {
    void trigger(pipeline.id, button, root);
  });
  return button;
}

async function trigger(
  pipelineId: string,
  button: HTMLButtonElement,
  root: HTMLElement,
): Promise<void> {
  button.disabled = true;
  button.textContent = "Starting…";
  try {
    const job = await runPipeline(pipelineId);
    navigate(`/jobs/${job.id}`);
  } catch (error) {
    button.disabled = false;
    button.textContent = "Run";
    flash(root, describe(error));
  }
}

function flash(root: HTMLElement, message: string): void {
  const banner = el("p", { class: "error" }, [message]);
  root.prepend(banner);
  setTimeout(() => banner.remove(), 5000);
}

function describe(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "Unauthorized — set a token to act.";
    if (error.status === 403) return "Forbidden — you lack permission.";
    return `Error ${error.status}: ${error.message}`;
  }
  return error instanceof Error ? error.message : String(error);
}
