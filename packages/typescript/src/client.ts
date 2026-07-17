import { request } from "./http.js";
import { AuthenticationError, NamiFusionError, TaskFailedError } from "./errors.js";
import type {
  ClientOptions,
  ListTasksParams,
  ListTasksResult,
  RunOptions,
  RunResult,
  SubscribeOptions,
  Task,
} from "./types.js";

const DEFAULT_BASE_URL = "https://www.namifusion.com/api/v1/marketplace";
const DEFAULT_MAX_RETRIES = 2;
const DEFAULT_TIMEOUT_MS = 60_000;

const DEFAULT_POLL_INTERVAL_MS = 2_000;
const POLL_INTERVAL_BACKOFF_FACTOR = 1.5;
const POLL_INTERVAL_CAP_MS = 10_000;
/** 30 minutes — a wavespeed-python-style lenient default; a tighter
 * default (e.g. 10 minutes) would spuriously time out slower video models. */
const DEFAULT_SUBSCRIBE_TIMEOUT_MS = 1_800_000;

const SDK_VERSION = "0.1.0";
const USER_AGENT = `namifusion-js/${SDK_VERSION}`;

/** Reads `NAMIFUSION_API_KEY` from the environment, but only when a
 * `process` global actually exists (Node). The optional chain on
 * `process.env` keeps this safe to call unconditionally in a browser
 * bundle, where `process` is undefined. */
function apiKeyFromEnv(): string | undefined {
  if (typeof process === "undefined") return undefined;
  return process.env?.NAMIFUSION_API_KEY;
}

/** Generates a UUIDv4 string for use as an auto Idempotency-Key.
 *
 * Prefers `globalThis.crypto.randomUUID()`, but falls back to a
 * `Math.random()`-based UUIDv4 when it's unavailable. That happens on
 * Node 18: the SDK's floor is `engines: ">=18"`, but the Web Crypto API
 * was only exposed as a global (unflagged) starting in Node 19 — on
 * Node 18 it lives behind `--experimental-global-webcrypto` and
 * `globalThis.crypto` is `undefined` by default, so calling
 * `crypto.randomUUID()` directly throws a ReferenceError. An idempotency
 * key only needs to be unique-enough to dedupe retries of a single call,
 * not cryptographically unpredictable, so the fallback's non-CSPRNG
 * randomness is an acceptable tradeoff here. */
function generateIdempotencyKey(): string {
  const webCrypto = globalThis.crypto;
  if (webCrypto?.randomUUID) {
    return webCrypto.randomUUID();
  }

  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

/** Resolves after `ms` milliseconds, or rejects immediately with
 * `signal.reason` if `signal` fires while waiting. This mirrors http.ts's
 * private `sleep()` helper; duplicated here rather than imported since
 * http.ts's public surface is intentionally limited to `request` (see its
 * module doc) and this is a small, self-contained primitive. */
function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  if (ms <= 0) return Promise.resolve();

  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      cleanup();
      resolve();
    }, ms);

    function onAbort(): void {
      cleanup();
      reject(signal?.reason ?? new Error("Aborted"));
    }

    function cleanup(): void {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    }

    if (signal) {
      if (signal.aborted) {
        onAbort();
        return;
      }
      signal.addEventListener("abort", onAbort, { once: true });
    }
  });
}

/** Internal per-call overrides `subscribe()` threads into its `run()` /
 * `getTask()` calls: the caller's `AbortSignal` (so an abort interrupts the
 * in-flight HTTP request, not just the polling gap) and a
 * hard-deadline-converged per-request timeout. Not part of the public API —
 * omit it (every public call site does) and each method behaves exactly as
 * its contract signature promises. */
interface RequestOverrides {
  signal?: AbortSignal;
  timeoutMs?: number;
}

/**
 * Validates that a 2xx response body parsed into the JSON object the API
 * contract promises, throwing a `NamiFusionError` (carrying the raw body on
 * `.detail`) when it didn't. A misbehaving gateway returning `200
 * text/html`, or an object missing fields every response of this kind must
 * have, would otherwise slip through as a malformed `RunResult`/`Task` and
 * only surface later as an `undefined` `.task_uuid`. Catch it at the
 * boundary instead. Mirrors _types.py's `_from_dict` non-Mapping guard.
 */
function assertObjectResponse(
  body: unknown,
  context: string,
  requiredKeys: readonly string[],
): void {
  if (body === null || typeof body !== "object" || Array.isArray(body)) {
    throw new NamiFusionError(
      `Malformed ${context} response: expected a JSON object from the NamiFusion API`,
      0,
      { detail: body },
    );
  }
  const record = body as Record<string, unknown>;
  const missing = requiredKeys.filter((key) => record[key] === undefined);
  if (missing.length > 0) {
    throw new NamiFusionError(
      `Malformed ${context} response: missing expected field(s) ${missing.join(", ")}`,
      0,
      { detail: body },
    );
  }
}

/**
 * Client for the NamiFusion AI model marketplace API.
 *
 * Mirrors the cross-repo shared contract's "SDK 公共 API 契约" section
 * (docs/superpowers/plans/2026-07-17-sdk-contract.md).
 */
export class NamiFusion {
  private readonly apiKey: string;
  private readonly baseUrl: string;
  private readonly maxRetries: number;
  private readonly timeoutMs: number;

  constructor(options: ClientOptions = {}) {
    const apiKey = options.apiKey ?? apiKeyFromEnv();
    if (!apiKey) {
      throw new AuthenticationError(
        "Missing API key: pass `apiKey` to the NamiFusion constructor, or set the NAMIFUSION_API_KEY environment variable",
      );
    }

    this.apiKey = apiKey;
    this.baseUrl = (options.baseUrl ?? DEFAULT_BASE_URL).replace(/\/+$/, "");
    this.maxRetries = options.maxRetries ?? DEFAULT_MAX_RETRIES;
    this.timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  }

  /**
   * Submits a model run without waiting for completion — the API is
   * always async. Prefer `subscribe` unless you specifically want to poll
   * (or receive a webhook) yourself.
   *
   * `modelId` is concatenated into the URL path as-is (e.g.
   * `google/nano-banana-pro/text-to-image` — its embedded slashes are
   * intentionally not percent-encoded, matching the server's `:path`
   * route parameter).
   *
   * When `idempotencyKey` is omitted, one is generated via
   * `generateIdempotencyKey()` and reused for every retry attempt of this
   * request (http.ts's `request()` builds the headers object once and
   * reuses it across retries), so retrying a POST /run is always safe.
   */
  async run(
    modelId: string,
    options: RunOptions,
    overrides: RequestOverrides = {},
  ): Promise<RunResult> {
    const idempotencyKey = options.idempotencyKey ?? generateIdempotencyKey();
    const body: Record<string, unknown> = { input: options.input };
    if (options.webhookUrl !== undefined) {
      body.webhook_url = options.webhookUrl;
    }

    const result = await request<RunResult>({
      method: "POST",
      url: `${this.baseUrl}/run/${modelId}`,
      apiKey: this.apiKey,
      body,
      headers: { "Idempotency-Key": idempotencyKey },
      timeoutMs: overrides.timeoutMs ?? this.timeoutMs,
      maxRetries: this.maxRetries,
      userAgent: USER_AGENT,
      signal: overrides.signal,
    });
    assertObjectResponse(result, "run", ["task_uuid", "status"]);
    return result;
  }

  /** Fetches the current status of a previously submitted task. */
  async getTask(taskUuid: string, overrides: RequestOverrides = {}): Promise<Task> {
    const result = await request<Task>({
      method: "GET",
      url: `${this.baseUrl}/run/tasks/${taskUuid}`,
      apiKey: this.apiKey,
      timeoutMs: overrides.timeoutMs ?? this.timeoutMs,
      maxRetries: this.maxRetries,
      userAgent: USER_AGENT,
      signal: overrides.signal,
    });
    assertObjectResponse(result, "getTask", ["task_uuid", "status"]);
    return result;
  }

  /**
   * Lists the caller's tasks. Query params mirror be_mono's API-key
   * `GET /run/tasks` endpoint (`skip`, `limit`, `model_id`, `status`);
   * see `ListTasksParams` for details.
   */
  async listTasks(params: ListTasksParams = {}): Promise<ListTasksResult> {
    const query = new URLSearchParams();
    if (params.skip !== undefined) query.set("skip", String(params.skip));
    if (params.limit !== undefined) query.set("limit", String(params.limit));
    if (params.modelId !== undefined) query.set("model_id", params.modelId);
    if (params.status !== undefined) query.set("status", params.status);

    const qs = query.toString();
    const url = `${this.baseUrl}/run/tasks${qs ? `?${qs}` : ""}`;

    const result = await request<ListTasksResult>({
      method: "GET",
      url,
      apiKey: this.apiKey,
      timeoutMs: this.timeoutMs,
      maxRetries: this.maxRetries,
      userAgent: USER_AGENT,
    });
    assertObjectResponse(result, "listTasks", ["total", "items"]);
    return result;
  }

  /**
   * Submits a run and polls `getTask` until it reaches a terminal state.
   * The poll interval starts at `pollIntervalMs` (default 2000ms), backs
   * off x1.5 after every poll, capped at 10_000ms. The whole call is
   * bounded by a total `timeoutMs` (default 1_800_000ms / 30 minutes).
   *
   * Resolves with the terminal `Task` when `status === "completed"`.
   * Throws `TaskFailedError` (carrying the terminal `Task`) when
   * `status` is `"failed"` or `"cancelled"`. Throws a plain
   * `NamiFusionError` if the total timeout elapses first — the SDK's
   * error taxonomy has no dedicated timeout class, and this isn't tied to
   * a single HTTP response, so (like `TaskFailedError`) it goes through
   * the base class directly rather than inventing a new one.
   *
   * `onUpdate` fires once per poll (not for the initial `run()` submission
   * response, which is a `RunResult`, not a `Task`). `signal`, if given, is
   * threaded into every internal `run()`/`getTask()` HTTP request, so an
   * abort interrupts a request already in flight (not just the polling gap)
   * and rejects with `signal.reason` as soon as it fires. `timeoutMs` is a
   * hard deadline measured from entry (see below).
   */
  async subscribe(modelId: string, options: SubscribeOptions): Promise<Task> {
    const {
      pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
      timeoutMs = DEFAULT_SUBSCRIBE_TIMEOUT_MS,
      onUpdate,
      signal,
      ...runOptions
    } = options;

    if (signal?.aborted) {
      throw signal.reason ?? new Error("Aborted");
    }

    // Hard deadline: the clock starts *before* the initial submit, so the
    // whole subscribe() call — first run() included — is bounded by
    // `timeoutMs`. Each internal HTTP request's own timeout is converged to
    // max(1ms, min(client timeout, remaining budget)) so a single hung
    // request can't block past the deadline (the polling-gap check below
    // only fires between requests, not during one).
    const startedAt = Date.now();
    const perRequestTimeoutMs = (): number =>
      Math.max(1, Math.min(this.timeoutMs, startedAt + timeoutMs - Date.now()));

    const submitted = await this.run(modelId, runOptions, {
      signal,
      timeoutMs: perRequestTimeoutMs(),
    });
    let interval = pollIntervalMs;

    for (;;) {
      const elapsed = Date.now() - startedAt;
      if (elapsed >= timeoutMs) {
        throw new NamiFusionError(
          `subscribe() timed out after ${timeoutMs}ms waiting for task ${submitted.task_uuid} to reach a terminal state`,
          0,
          { detail: { task_uuid: submitted.task_uuid, timeout_ms: timeoutMs } },
        );
      }

      await sleep(Math.min(interval, timeoutMs - elapsed), signal);

      const task = await this.getTask(submitted.task_uuid, {
        signal,
        timeoutMs: perRequestTimeoutMs(),
      });
      if (signal?.aborted) {
        throw signal.reason ?? new Error("Aborted");
      }
      onUpdate?.(task);

      if (task.status === "completed") {
        return task;
      }
      if (task.status === "failed" || task.status === "cancelled") {
        throw new TaskFailedError(
          task.error_message ?? `Task ${task.task_uuid} ${task.status}`,
          task,
        );
      }

      interval = Math.min(interval * POLL_INTERVAL_BACKOFF_FACTOR, POLL_INTERVAL_CAP_MS);
    }
  }
}
