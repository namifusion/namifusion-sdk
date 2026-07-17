import { errorFromResponse, parseRetryAfterSeconds } from "./errors.js";

/** Options accepted by {@link request}. This is the SDK's internal
 * transport primitive — `client.ts` (Task 3) is the only intended caller. */
export interface RequestOptions {
  method: string;
  url: string;
  apiKey: string;
  body?: unknown;
  headers?: Record<string, string>;
  /** Per-attempt timeout in ms. Each retry gets a fresh timeout window. */
  timeoutMs: number;
  /** Number of retries *in addition to* the initial attempt. */
  maxRetries: number;
  userAgent: string;
  signal?: AbortSignal;
}

const RETRYABLE_STATUSES = new Set([429, 502, 503, 504]);

/**
 * Computes the exponential backoff delay (ms) before retry attempt
 * `attempt` (0-indexed: 0 = delay before the 1st retry). Base 500ms,
 * doubling per attempt, capped at 8000ms, with up to ±20% jitter applied
 * on top so concurrent clients don't retry in lockstep.
 *
 * `random` is injectable (defaults to `Math.random`) so tests can assert
 * exact values instead of ranges.
 */
export function computeBackoffDelayMs(attempt: number, random: () => number = Math.random): number {
  const base = Math.min(500 * 2 ** attempt, 8000);
  const jitterFactor = 1 + (random() * 0.4 - 0.2); // [0.8, 1.2]
  return Math.max(0, Math.round(base * jitterFactor));
}

/** Resolves after `ms` milliseconds, or rejects immediately if `signal`
 * fires while waiting (used so an external cancellation interrupts a
 * pending retry backoff instead of sleeping it out). */
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

function buildHeaders(opts: RequestOptions, hasBody: boolean): Headers {
  const headers = new Headers(opts.headers);
  headers.set("Authorization", `Bearer ${opts.apiKey}`);
  headers.set("User-Agent", opts.userAgent);
  if (hasBody && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  return headers;
}

async function readJsonBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    // Non-JSON body (shouldn't happen against this API, but don't crash on it).
    return text;
  }
}

/** ms to wait per the `Retry-After` header (seconds, capped at 60s), or
 * `undefined` when the header is absent/unparseable (e.g. the monthly-quota
 * flavor of 429, which carries no Retry-After). */
function retryAfterDelayMs(headers: Headers): number | undefined {
  const seconds = parseRetryAfterSeconds(headers);
  return seconds === undefined ? undefined : seconds * 1000;
}

/**
 * Performs a single logical HTTP request with timeout + retry handling.
 *
 * Retries (network errors, 429, 502, 503, 504) use exponential backoff
 * (see {@link computeBackoffDelayMs}), except a 429 with a `Retry-After`
 * header waits that many seconds (capped at 60s) instead. Any other 4xx
 * throws immediately via `errorFromResponse` without retrying.
 *
 * Network errors are retried the same way — this covers both `fetch`
 * rejecting (including timeout-induced aborts) *and* a failure while
 * reading the response body (`response.text()` rejecting mid-stream).
 * Once retries are exhausted the *original* error is re-thrown unchanged
 * (no NamiFusionError wrapping) since it isn't an HTTP response to map. An
 * external `signal` abort is never retried — it
 * propagates immediately, including before the first attempt (no `fetch`
 * call is made at all) and while a retry backoff is pending.
 */
export async function request<T>(opts: RequestOptions): Promise<T> {
  if (opts.signal?.aborted) {
    throw opts.signal.reason ?? new Error("Aborted");
  }

  const bodyText = opts.body === undefined ? undefined : JSON.stringify(opts.body);
  const headers = buildHeaders(opts, bodyText !== undefined);

  let attempt = 0;

  for (;;) {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      controller.abort(new DOMException("Request timed out", "TimeoutError"));
    }, opts.timeoutMs);

    const forwardAbort = (): void => controller.abort(opts.signal?.reason);
    if (opts.signal) {
      if (opts.signal.aborted) {
        forwardAbort();
      } else {
        opts.signal.addEventListener("abort", forwardAbort, { once: true });
      }
    }

    try {
      let response: Response;
      let parsedBody: unknown;
      try {
        response = await fetch(opts.url, {
          method: opts.method,
          headers,
          body: bodyText,
          signal: controller.signal,
        });
        // Read the body inside the same try/catch as `fetch` so a body-read
        // network error (`response.text()` rejecting mid-stream) is treated
        // exactly like a `fetch` rejection: retried, then re-thrown raw once
        // retries are exhausted. Reading once here also serves both the ok
        // and error branches below.
        parsedBody = await readJsonBody(response);
      } catch (err) {
        if (opts.signal?.aborted || attempt >= opts.maxRetries) {
          throw err;
        }
        await sleep(computeBackoffDelayMs(attempt), opts.signal);
        attempt += 1;
        continue;
      }

      if (response.ok) {
        return parsedBody as T;
      }

      if (RETRYABLE_STATUSES.has(response.status) && attempt < opts.maxRetries) {
        const delay =
          response.status === 429
            ? (retryAfterDelayMs(response.headers) ?? computeBackoffDelayMs(attempt))
            : computeBackoffDelayMs(attempt);
        await sleep(delay, opts.signal);
        attempt += 1;
        continue;
      }

      throw errorFromResponse(response.status, parsedBody, response.headers);
    } finally {
      clearTimeout(timer);
      if (opts.signal) opts.signal.removeEventListener("abort", forwardAbort);
    }
  }
}
