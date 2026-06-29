// Typed client for the Master's read-only + control API. The SPA is served from
// the same origin as the API, so paths are relative ("" base). Every call
// carries the bearer token when one is set.

import { authHeaders } from "./auth.ts";
import type { Job, Page, Pipeline } from "./types.ts";

const API = "/api/v1";

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { ...authHeaders() };
  if (init?.body !== undefined && init.body !== null) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    throw new ApiError(response.status, await errorText(response));
  }
  return (await response.json()) as T;
}

async function errorText(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // Non-JSON error body; fall through to the status line.
  }
  return `${response.status} ${response.statusText}`;
}

export function listPipelines(limit = 50, offset = 0): Promise<Page<Pipeline>> {
  return request<Page<Pipeline>>(`${API}/pipelines?limit=${limit}&offset=${offset}`);
}

export function getPipeline(id: string): Promise<Pipeline> {
  return request<Pipeline>(`${API}/pipelines/${encodeURIComponent(id)}`);
}

export function listJobs(limit = 50, offset = 0): Promise<Page<Job>> {
  return request<Page<Job>>(`${API}/jobs?limit=${limit}&offset=${offset}`);
}

export function getJob(id: string): Promise<Job> {
  return request<Job>(`${API}/jobs/${encodeURIComponent(id)}`);
}

export function runPipeline(id: string, ref?: string): Promise<Job> {
  return request<Job>(`${API}/pipelines/${encodeURIComponent(id)}/run`, {
    method: "POST",
    body: JSON.stringify({ ref: ref ?? null }),
  });
}

export function cancelJob(id: string): Promise<Job> {
  return request<Job>(`${API}/jobs/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
  });
}
