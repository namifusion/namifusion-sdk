import { afterEach, describe, expect, it, vi } from "vitest";
import { computeBackoffDelayMs, request } from "../src/http.js";
import {
  AuthenticationError,
  InsufficientCreditsError,
  InvalidRequestError,
  parseRetryAfterSeconds,
  RateLimitError,
  ServerError,
} from "../src/errors.js";

const USER_AGENT = "namifusion-js/0.1.0-test";

function jsonResponse(
  status: number,
  body: unknown,
  headers: Record<string, string> = {},
): Response {
  const text = body === undefined ? "" : JSON.stringify(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(headers),
    text: async () => text,
  } as unknown as Response;
}

function baseOpts(overrides: Partial<Parameters<typeof request>[0]> = {}) {
  return {
    method: "GET",
    url: "https://test.namifusion.com/api/v1/marketplace/run/tasks/abc",
    apiKey: "sk-test-key",
    timeoutMs: 5000,
    maxRetries: 2,
    userAgent: USER_AGENT,
    ...overrides,
  } as Parameters<typeof request>[0];
}

describe("http.request", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("sends Authorization and User-Agent headers, and Content-Type for a JSON body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { ok: true }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const result = await request(
      baseOpts({ method: "POST", body: { input: { foo: "bar" } } }),
    );

    expect(result).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://test.namifusion.com/api/v1/marketplace/run/tasks/abc");
    const headers = init.headers as Headers;
    expect(headers.get("authorization")).toBe("Bearer sk-test-key");
    expect(headers.get("user-agent")).toBe(USER_AGENT);
    expect(headers.get("content-type")).toBe("application/json");
    expect(init.body).toBe(JSON.stringify({ input: { foo: "bar" } }));
  });

  it("merges custom headers passed via opts.headers", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, {}));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    await request(baseOpts({ headers: { "Idempotency-Key": "abc-123" } }));

    const [, init] = fetchMock.mock.calls[0];
    const headers = init.headers as Headers;
    expect(headers.get("idempotency-key")).toBe("abc-123");
  });

  it("maps 401 to AuthenticationError without retrying", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(401, { detail: "Invalid API key" }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    await expect(request(baseOpts())).rejects.toBeInstanceOf(AuthenticationError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("parses a plain string 402 detail", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(402, { detail: "Insufficient credits" }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const err = (await request(baseOpts()).catch((e) => e)) as InsufficientCreditsError;
    expect(err).toBeInstanceOf(InsufficientCreditsError);
    expect(err.status).toBe(402);
    expect(err.detail).toBe("Insufficient credits");
    expect(err.code).toBeUndefined();
  });

  it("parses a structured {code,message} 402 detail", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(402, {
        detail: { code: "insufficient_credits", message: "Not enough credits" },
      }),
    );
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const err = (await request(baseOpts()).catch((e) => e)) as InsufficientCreditsError;
    expect(err).toBeInstanceOf(InsufficientCreditsError);
    expect(err.status).toBe(402);
    expect(err.code).toBe("insufficient_credits");
    expect(err.message).toBe("Not enough credits");
    expect(err.detail).toEqual({
      code: "insufficient_credits",
      message: "Not enough credits",
    });
  });

  it("retries a 429 honoring Retry-After (seconds) and eventually succeeds", async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(429, { detail: "Too many requests" }, { "Retry-After": "2" }),
      )
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    let resolved = false;
    const promise = request(baseOpts()).then((r) => {
      resolved = true;
      return r;
    });

    // just under 2s: must not have retried/resolved yet
    await vi.advanceTimersByTimeAsync(1900);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(resolved).toBe(false);

    // cross the 2s Retry-After boundary
    await vi.advanceTimersByTimeAsync(150);
    const result = await promise;

    expect(resolved).toBe(true);
    expect(result).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("exhausts retries on repeated 503 and throws ServerError", async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(503, { detail: "Service unavailable" }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const promise = request(baseOpts({ maxRetries: 2 }));
    const assertion = expect(promise).rejects.toBeInstanceOf(ServerError);
    await vi.runAllTimersAsync();
    await assertion;

    // 1 initial attempt + 2 retries = 3 calls
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("exhausts retries on repeated 429 and throws RateLimitError carrying retryAfter", async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse(429, { detail: "Too many requests" }, { "Retry-After": "3" }),
      );
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const promise = request(baseOpts({ maxRetries: 2 }));
    const assertion = expect(promise).rejects.toBeInstanceOf(RateLimitError);
    await vi.runAllTimersAsync();
    await assertion;

    const err = (await promise.catch((e) => e)) as RateLimitError;
    expect(err.retryAfter).toBe(3);

    // 1 initial attempt + 2 retries = 3 calls
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("does not retry a 400 (non-429 4xx)", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(400, { detail: "Bad request" }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    await expect(request(baseOpts({ maxRetries: 2 }))).rejects.toBeInstanceOf(
      InvalidRequestError,
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("aborts the request once timeoutMs elapses", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn((_url: string, init: RequestInit) => {
      return new Promise((_resolve, reject) => {
        const signal = init.signal as AbortSignal;
        signal.addEventListener("abort", () => {
          const err = new Error("The operation was aborted");
          err.name = "AbortError";
          reject(err);
        });
      });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const promise = request(baseOpts({ timeoutMs: 50, maxRetries: 0 }));
    const assertion = expect(promise).rejects.toMatchObject({ name: "AbortError" });
    await vi.advanceTimersByTimeAsync(50);
    await assertion;

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("rejects immediately with the raw network error once retries are exhausted", async () => {
    vi.useFakeTimers();
    const networkError = new TypeError("fetch failed");
    const fetchMock = vi.fn().mockRejectedValue(networkError);
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const promise = request(baseOpts({ maxRetries: 1 }));
    const assertion = expect(promise).rejects.toBe(networkError);
    await vi.runAllTimersAsync();
    await assertion;

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("rejects immediately and never calls fetch when opts.signal is already aborted", async () => {
    const controller = new AbortController();
    const abortReason = new Error("cancelled before the call even started");
    controller.abort(abortReason);

    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    await expect(
      request(baseOpts({ signal: controller.signal })),
    ).rejects.toBe(abortReason);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("aborts an in-flight fetch immediately when opts.signal fires mid-flight (never-resolving fetch)", async () => {
    const controller = new AbortController();
    const abortReason = new Error("caller gave up mid-flight");

    // Never resolves on its own — the only way this settles is via the
    // AbortSignal forwarded into `init.signal`, mirroring how a real
    // fetch()/undici implementation rejects with `signal.reason` once its
    // controller aborts.
    const fetchMock = vi.fn((_url: string, init: RequestInit) => {
      return new Promise((_resolve, reject) => {
        const signal = init.signal as AbortSignal;
        signal.addEventListener("abort", () => reject(signal.reason));
      });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const promise = request(baseOpts({ signal: controller.signal }));
    const assertion = expect(promise).rejects.toBe(abortReason);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    controller.abort(abortReason);
    await assertion;

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("interrupts a pending retry backoff immediately when opts.signal aborts mid-wait", async () => {
    vi.useFakeTimers();
    // Pin the jitter so the 503 backoff delay for attempt 0 is exactly 500ms
    // (base 500 * jitterFactor 1.0), making the "abort well before the
    // timer fires" assertion below non-flaky.
    vi.spyOn(Math, "random").mockReturnValue(0.5);

    const controller = new AbortController();
    const abortReason = new Error("caller gave up while we were backing off");

    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(503, { detail: "Service unavailable" }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const promise = request(baseOpts({ signal: controller.signal, maxRetries: 2 }));
    const assertion = expect(promise).rejects.toBe(abortReason);

    // Let the first (503) attempt resolve and the retry backoff timer get
    // scheduled, but stay well short of the 500ms delay.
    await vi.advanceTimersByTimeAsync(100);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    controller.abort(abortReason);
    await assertion;

    // Draining any remaining timers must not trigger a second fetch call —
    // the pending backoff was cancelled, not merely raced.
    await vi.runAllTimersAsync();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("computeBackoffDelayMs", () => {
  it("grows exponentially from a 500ms base and caps at 8s", () => {
    const noJitter = () => 0.5; // midpoint -> jitter factor 1.0
    expect(computeBackoffDelayMs(0, noJitter)).toBe(500);
    expect(computeBackoffDelayMs(1, noJitter)).toBe(1000);
    expect(computeBackoffDelayMs(2, noJitter)).toBe(2000);
    expect(computeBackoffDelayMs(4, noJitter)).toBe(8000); // 500*2^4=8000, already capped
    expect(computeBackoffDelayMs(10, noJitter)).toBe(8000); // stays capped
  });

  it("applies up to +-20% jitter", () => {
    expect(computeBackoffDelayMs(1, () => 0)).toBe(800); // 1000 * 0.8
    expect(computeBackoffDelayMs(1, () => 1)).toBe(1200); // 1000 * 1.2
  });
});

describe("parseRetryAfterSeconds", () => {
  it("caps a Retry-After above 30s down to 30", () => {
    expect(parseRetryAfterSeconds(new Headers({ "Retry-After": "100" }))).toBe(30);
  });

  it("passes through values at or below the 30s cap unchanged", () => {
    expect(parseRetryAfterSeconds(new Headers({ "Retry-After": "30" }))).toBe(30);
    expect(parseRetryAfterSeconds(new Headers({ "Retry-After": "5" }))).toBe(5);
  });

  it("returns undefined when the header is absent or unparseable", () => {
    expect(parseRetryAfterSeconds(new Headers())).toBeUndefined();
    expect(parseRetryAfterSeconds(new Headers({ "Retry-After": "not-a-number" }))).toBeUndefined();
  });
});
