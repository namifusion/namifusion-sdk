import type { Task } from "./types.js";

/** Extra fields every NamiFusionError subclass can carry, beyond the
 * required (message, status) pair. */
export interface NamiFusionErrorOptions {
  /** Machine-readable error code, when the API supplied a structured
   * `{"detail": {"code": ..., "message": ...}}` body (seen on 402). */
  code?: string;
  /** Raw `detail` value from the FastAPI `{"detail": ...}` error body
   * (string or structured object), preserved for callers who need more
   * than `message`/`code`. */
  detail?: unknown;
  /** Underlying cause (e.g. the network error that triggered a final
   * failure), wired through to Error.cause when provided. */
  cause?: unknown;
}

/** Base class for all errors raised by the NamiFusion SDK. */
export class NamiFusionError extends Error {
  /** HTTP status code, when this error originated from an HTTP response.
   * 0 for errors that are not tied to a single HTTP response
   * (see TaskFailedError). */
  readonly status: number;
  readonly code?: string;
  readonly detail?: unknown;
  /** Own `cause` property (not `super(message, {cause})`) since the
   * project's `tsconfig` targets ES2020, whose lib doesn't type the
   * ES2022 Error `cause` constructor option. Node >=18 (this SDK's
   * floor) supports `Error.cause` at runtime regardless. */
  readonly cause?: unknown;

  constructor(message: string, status: number, options: NamiFusionErrorOptions = {}) {
    super(message);
    this.name = "NamiFusionError";
    this.status = status;
    this.code = options.code;
    this.detail = options.detail;
    this.cause = options.cause;
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

/** 401 — invalid or missing credentials. */
export class AuthenticationError extends NamiFusionError {
  constructor(message = "Authentication failed", options: NamiFusionErrorOptions = {}) {
    super(message, 401, options);
    this.name = "AuthenticationError";
  }
}

/** 402 — account lacks sufficient credits to run this request. */
export class InsufficientCreditsError extends NamiFusionError {
  constructor(message = "Insufficient credits", options: NamiFusionErrorOptions = {}) {
    super(message, 402, options);
    this.name = "InsufficientCreditsError";
  }
}

/** 403 — authenticated but not authorized for this resource. */
export class ForbiddenError extends NamiFusionError {
  constructor(message = "Forbidden", options: NamiFusionErrorOptions = {}) {
    super(message, 403, options);
    this.name = "ForbiddenError";
  }
}

/** 404 — resource (e.g. task_uuid) not found. */
export class NotFoundError extends NamiFusionError {
  constructor(message = "Not found", options: NamiFusionErrorOptions = {}) {
    super(message, 404, options);
    this.name = "NotFoundError";
  }
}

/** 400 or 422 — malformed or invalid request. */
export class InvalidRequestError extends NamiFusionError {
  constructor(message = "Invalid request", status = 400, options: NamiFusionErrorOptions = {}) {
    super(message, status, options);
    this.name = "InvalidRequestError";
  }
}

/** 429 — rate limited (per-second throttling) or monthly quota exceeded.
 * `retryAfter` (seconds) is only present for the throttling case. */
export class RateLimitError extends NamiFusionError {
  readonly retryAfter?: number;

  constructor(
    message = "Rate limit exceeded",
    options: NamiFusionErrorOptions & { retryAfter?: number } = {},
  ) {
    super(message, 429, options);
    this.name = "RateLimitError";
    this.retryAfter = options.retryAfter;
  }
}

/** 5xx — server-side failure. */
export class ServerError extends NamiFusionError {
  constructor(message = "Server error", status = 500, options: NamiFusionErrorOptions = {}) {
    super(message, status, options);
    this.name = "ServerError";
  }
}

/** Thrown by `client.subscribe()` when the task reaches a terminal
 * `failed`/`cancelled` state. Not an HTTP-response error, so `status` is 0;
 * the terminal Task is attached for inspection. */
export class TaskFailedError extends NamiFusionError {
  readonly task: Task;

  constructor(message: string, task: Task, options: NamiFusionErrorOptions = {}) {
    super(message, 0, options);
    this.name = "TaskFailedError";
    this.task = task;
  }
}

interface ParsedErrorBody {
  message: string;
  code?: string;
  detail?: unknown;
}

/** Parses the FastAPI `{"detail": ...}` error envelope. `detail` is either
 * a plain string, or (seen on 402) a structured `{code, message}` object. */
function parseErrorBody(body: unknown): ParsedErrorBody {
  if (body && typeof body === "object" && "detail" in (body as Record<string, unknown>)) {
    const detail = (body as Record<string, unknown>).detail;

    if (typeof detail === "string") {
      return { message: detail, detail };
    }

    if (detail && typeof detail === "object") {
      const structured = detail as Record<string, unknown>;
      const code = typeof structured.code === "string" ? structured.code : undefined;
      const message =
        typeof structured.message === "string" ? structured.message : JSON.stringify(detail);
      return { message, code, detail };
    }

    if (detail !== undefined && detail !== null) {
      return { message: String(detail), detail };
    }
  }

  return { message: "Request failed", detail: body };
}

/** Parses the `Retry-After` header as a whole number of seconds, capped at
 * 60s. Returns `undefined` when absent or unparseable (e.g. the
 * monthly-quota flavor of 429, which carries no Retry-After). Exported so
 * http.ts's retry loop can reuse the same parsing/cap logic.
 *
 * The 60s cap matches be_mono's 60s rate-limit window (2026-07-17 holistic
 * review): a shorter 30s cap would retry back inside the same window and
 * necessarily eat a second 429. */
export function parseRetryAfterSeconds(headers: Headers): number | undefined {
  const raw = headers.get("retry-after");
  if (!raw) return undefined;
  const seconds = Number(raw);
  if (!Number.isFinite(seconds) || seconds < 0) return undefined;
  return Math.min(seconds, 60);
}

/** Maps an HTTP error response to the corresponding NamiFusionError
 * subclass, parsing the FastAPI `{"detail": ...}` body along the way. */
export function errorFromResponse(status: number, body: unknown, headers: Headers): NamiFusionError {
  const { message, code, detail } = parseErrorBody(body);

  switch (status) {
    case 401:
      return new AuthenticationError(message, { code, detail });
    case 402:
      return new InsufficientCreditsError(message, { code, detail });
    case 403:
      return new ForbiddenError(message, { code, detail });
    case 404:
      return new NotFoundError(message, { code, detail });
    case 429:
      return new RateLimitError(message, {
        code,
        detail,
        retryAfter: parseRetryAfterSeconds(headers),
      });
    case 400:
    case 422:
      return new InvalidRequestError(message, status, { code, detail });
    default:
      if (status >= 500) {
        return new ServerError(message, status, { code, detail });
      }
      // Fallback for any other 4xx/unexpected status: still a real
      // NamiFusionError, just without a more specific subclass.
      return new NamiFusionError(message, status, { code, detail });
  }
}
