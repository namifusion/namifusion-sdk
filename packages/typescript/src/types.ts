/**
 * Public NamiFusion SDK data types.
 *
 * These mirror the shared cross-repo contract
 * (docs/superpowers/plans/2026-07-17-sdk-contract.md, "SDK 公共 API 契约" section).
 * Field names intentionally stay snake_case where they pass through the API
 * response as-is (Task/RunResult), and camelCase for SDK-authored option bags.
 */

/** Public task lifecycle. Internal `polling`/`waiting_callback` states are
 * mapped to `processing` server-side before reaching the client. */
export type TaskStatus = "pending" | "processing" | "completed" | "failed" | "cancelled";

/** TaskStatusResponse, as returned by `GET /run/tasks/{task_uuid}` and by
 * webhook callbacks. */
export interface Task {
  task_uuid: string;
  model_id: string;
  status: TaskStatus;
  progress?: number | null;
  /** Shape depends on the model's output_schema. Output URLs are unsigned
   * COS CDN links with an ~7 day lifetime. */
  output?: Record<string, unknown> | null;
  cost_credits?: number | null;
  meta_info?: Record<string, unknown> | null;
  error_message?: string | null;
  created_at: string;
  completed_at?: string | null;
}

/** Response of `POST /run/{model_id}` — run is always async. */
export interface RunResult {
  task_uuid: string;
  status: TaskStatus;
  estimated_time?: number | null;
  output?: Record<string, unknown> | null;
  cost_credits?: number | null;
}

/** Options accepted by the `NamiFusion` client constructor. */
export interface ClientOptions {
  /** Falls back to the `NAMIFUSION_API_KEY` env var (Node only). Client
   * constructor throws AuthenticationError if neither is present. */
  apiKey?: string;
  /** Defaults to "https://www.namifusion.com/api/v1/marketplace". */
  baseUrl?: string;
  /** Defaults to 2. */
  maxRetries?: number;
  /** Per-request timeout in ms. Defaults to 60_000. */
  timeoutMs?: number;
}

/** Options for `client.run(modelId, options)`. */
export interface RunOptions {
  input: Record<string, unknown>;
  webhookUrl?: string;
  /** Auto-generated (UUID) when omitted. */
  idempotencyKey?: string;
}

/** Options for `client.subscribe(modelId, options)`. */
export interface SubscribeOptions extends RunOptions {
  /** Defaults to 2_000, backed off x1.5 per poll, capped at 10_000. */
  pollIntervalMs?: number;
  /** Total wait budget in ms. Defaults to 1_800_000 (30 minutes). */
  timeoutMs?: number;
  onUpdate?: (task: Task) => void;
  signal?: AbortSignal;
}

/** Query params for `client.listTasks(params)`, mirroring be_mono's
 * API-key `GET /run/tasks` endpoint (services/app_marketplace/api/v1/
 * endpoints/run.py `list_tasks`): `skip`/`limit` for pagination, optional
 * `model_id`/`status` filters. `status` is forwarded as-is to the server's
 * internal status filter (not restricted to the 5 public TaskStatus
 * values), so it's typed as a plain string rather than `TaskStatus`. */
export interface ListTasksParams {
  /** Defaults to 0 server-side. */
  skip?: number;
  /** Defaults to 20 server-side. */
  limit?: number;
  modelId?: string;
  status?: string;
}

/** Response of `GET /run/tasks`. */
export interface ListTasksResult {
  total: number;
  items: Task[];
}
