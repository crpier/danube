// Wire types mirroring the Master's read-only API schemas
// (`danube/api/schemas.py`). Kept deliberately permissive on enums: the backend
// is the source of truth, the UI only displays the strings it sends.

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface Pipeline {
  id: string;
  name: string;
  team_id: string;
  repo_url: string;
  branch_filter: string | null;
  cron_schedule: string | null;
  config_path: string;
  worker_image: string;
  max_duration_seconds: number;
  workspace_size_gb: number;
  created_at: string;
  updated_at: string;
}

export interface Job {
  id: string;
  pipeline_id: string;
  trigger_type: string;
  trigger_ref: string | null;
  status: string;
  runner_id: string | null;
  workspace_path: string | null;
  started_at: string | null;
  finished_at: string | null;
  log_path: string | null;
  failure_reason: string | null;
  created_at: string;
}

// Job statuses the backend considers finished; the log stream and the detail
// view stop polling/streaming once a job reaches one of these.
export const TERMINAL_STATUSES: ReadonlySet<string> = new Set([
  "success",
  "failure",
  "timeout",
  "cancelled",
]);

export function isTerminal(status: string): boolean {
  return TERMINAL_STATUSES.has(status);
}
