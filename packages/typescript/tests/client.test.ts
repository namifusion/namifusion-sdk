import { afterEach, describe, expect, it, vi } from "vitest";
import { NamiFusion } from "../src/client.js";
import { toDataUrl } from "../src/files.js";
import { AuthenticationError, NamiFusionError, TaskFailedError } from "../src/errors.js";
import type { Task } from "../src/types.js";

const BASE_URL = "https://test.namifusion.com/api/v1/marketplace";
const API_KEY = "sk-test-key";

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

function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    task_uuid: "t1",
    model_id: "acme/model-x",
    status: "pending",
    progress: null,
    output: null,
    cost_credits: 10,
    meta_info: null,
    error_message: null,
    created_at: "2026-07-17T00:00:00Z",
    completed_at: null,
    ...overrides,
  };
}

describe("NamiFusion constructor", () => {
  const originalEnv = process.env.NAMIFUSION_API_KEY;

  afterEach(() => {
    if (originalEnv === undefined) {
      delete process.env.NAMIFUSION_API_KEY;
    } else {
      process.env.NAMIFUSION_API_KEY = originalEnv;
    }
  });

  it("accepts an explicit apiKey", () => {
    delete process.env.NAMIFUSION_API_KEY;
    expect(() => new NamiFusion({ apiKey: "sk-explicit" })).not.toThrow();
  });

  it("falls back to the NAMIFUSION_API_KEY env var when apiKey is omitted", () => {
    process.env.NAMIFUSION_API_KEY = "sk-from-env";
    expect(() => new NamiFusion()).not.toThrow();
  });

  it("prefers the explicit apiKey over the env var when both are present", async () => {
    process.env.NAMIFUSION_API_KEY = "sk-from-env";
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { task_uuid: "t1", status: "pending" }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const client = new NamiFusion({ apiKey: "sk-explicit", baseUrl: BASE_URL });
    await client.run("acme/model-x", { input: {} });

    const [, init] = fetchMock.mock.calls[0];
    const headers = init.headers as Headers;
    expect(headers.get("authorization")).toBe("Bearer sk-explicit");
    vi.restoreAllMocks();
  });

  it("throws AuthenticationError when both apiKey and the env var are missing", () => {
    delete process.env.NAMIFUSION_API_KEY;
    expect(() => new NamiFusion()).toThrow(AuthenticationError);
  });

  it("defaults baseUrl to the production marketplace URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { task_uuid: "t1", status: "pending" }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const client = new NamiFusion({ apiKey: API_KEY });
    await client.run("acme/model-x", { input: {} });

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("https://www.namifusion.com/api/v1/marketplace/run/acme/model-x");
    vi.restoreAllMocks();
  });
});

describe("NamiFusion#run", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("POSTs {baseUrl}/run/{modelId} with modelId concatenated raw (slashes not encoded) and body {input}", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(200, { task_uuid: "t1", status: "pending", cost_credits: 5 }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const client = new NamiFusion({ apiKey: API_KEY, baseUrl: BASE_URL });
    const result = await client.run("google/nano-banana-pro/text-to-image", {
      input: { prompt: "hi" },
    });

    expect(result).toEqual({ task_uuid: "t1", status: "pending", cost_credits: 5 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${BASE_URL}/run/google/nano-banana-pro/text-to-image`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ input: { prompt: "hi" } });
  });

  it("auto-generates a UUID Idempotency-Key when idempotencyKey is omitted", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { task_uuid: "t1", status: "pending" }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const client = new NamiFusion({ apiKey: API_KEY, baseUrl: BASE_URL });
    await client.run("acme/model-x", { input: {} });

    const [, init] = fetchMock.mock.calls[0];
    const headers = init.headers as Headers;
    expect(headers.get("idempotency-key")).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
    );
  });

  it("falls back to a Math.random UUIDv4 when globalThis.crypto is unavailable (Node 18 without --experimental-global-webcrypto)", async () => {
    vi.stubGlobal("crypto", undefined);
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { task_uuid: "t1", status: "pending" }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const client = new NamiFusion({ apiKey: API_KEY, baseUrl: BASE_URL });
    await client.run("acme/model-x", { input: {} });
    await client.run("acme/model-x", { input: {} });

    const uuidV4Pattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
    const firstKey = (fetchMock.mock.calls[0][1].headers as Headers).get("idempotency-key");
    const secondKey = (fetchMock.mock.calls[1][1].headers as Headers).get("idempotency-key");
    expect(firstKey).toMatch(uuidV4Pattern);
    expect(secondKey).toMatch(uuidV4Pattern);
    expect(firstKey).not.toBe(secondKey);
  });

  it("sends the caller-supplied idempotencyKey verbatim when provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { task_uuid: "t1", status: "pending" }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const client = new NamiFusion({ apiKey: API_KEY, baseUrl: BASE_URL });
    await client.run("acme/model-x", { input: {}, idempotencyKey: "caller-key-123" });

    const [, init] = fetchMock.mock.calls[0];
    expect((init.headers as Headers).get("idempotency-key")).toBe("caller-key-123");
  });

  it("includes webhook_url in the body when webhookUrl is provided, omits it otherwise", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { task_uuid: "t1", status: "pending" }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    const client = new NamiFusion({ apiKey: API_KEY, baseUrl: BASE_URL });

    await client.run("acme/model-x", { input: { a: 1 }, webhookUrl: "https://example.com/hook" });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({
      input: { a: 1 },
      webhook_url: "https://example.com/hook",
    });

    fetchMock.mockClear();
    await client.run("acme/model-x", { input: { a: 1 } });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({ input: { a: 1 } });
  });

  it("reuses the same Idempotency-Key across an internal retry (retry-safe)", async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(503, { detail: "Service unavailable" }))
      .mockResolvedValueOnce(jsonResponse(200, { task_uuid: "t1", status: "pending" }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const client = new NamiFusion({ apiKey: API_KEY, baseUrl: BASE_URL, maxRetries: 1 });
    const promise = client.run("acme/model-x", { input: {} });
    await vi.runAllTimersAsync();
    const result = await promise;

    expect(result).toEqual({ task_uuid: "t1", status: "pending" });
    expect(fetchMock).toHaveBeenCalledTimes(2);

    const key1 = (fetchMock.mock.calls[0][1].headers as Headers).get("idempotency-key");
    const key2 = (fetchMock.mock.calls[1][1].headers as Headers).get("idempotency-key");
    expect(key1).toBeTruthy();
    expect(key1).toBe(key2);
  });

  it("generates a fresh Idempotency-Key for each independent call to run() (regression guard: the key must be generated per-call, not cached on the client)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { task_uuid: "t1", status: "pending" }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const client = new NamiFusion({ apiKey: API_KEY, baseUrl: BASE_URL });
    await client.run("acme/model-x", { input: {} });
    await client.run("acme/model-x", { input: {} });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const key1 = (fetchMock.mock.calls[0][1].headers as Headers).get("idempotency-key");
    const key2 = (fetchMock.mock.calls[1][1].headers as Headers).get("idempotency-key");
    expect(key1).toBeTruthy();
    expect(key2).toBeTruthy();
    expect(key1).not.toBe(key2);
  });
});

describe("NamiFusion#getTask", () => {
  afterEach(() => vi.restoreAllMocks());

  it("GETs {baseUrl}/run/tasks/{taskUuid}", async () => {
    const task = makeTask({ status: "processing" });
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, task));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const client = new NamiFusion({ apiKey: API_KEY, baseUrl: BASE_URL });
    const result = await client.getTask("t1");

    expect(result).toEqual(task);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${BASE_URL}/run/tasks/t1`);
    expect(init.method).toBe("GET");
  });
});

describe("NamiFusion#listTasks", () => {
  afterEach(() => vi.restoreAllMocks());

  it("GETs {baseUrl}/run/tasks with skip/limit/model_id/status query params", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { total: 0, items: [] }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const client = new NamiFusion({ apiKey: API_KEY, baseUrl: BASE_URL });
    await client.listTasks({ skip: 10, limit: 5, modelId: "acme/model-x", status: "completed" });

    const expectedQs = new URLSearchParams({
      skip: "10",
      limit: "5",
      model_id: "acme/model-x",
      status: "completed",
    }).toString();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${BASE_URL}/run/tasks?${expectedQs}`);
    expect(init.method).toBe("GET");
  });

  it("omits the query string entirely when called with no params", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { total: 0, items: [] }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const client = new NamiFusion({ apiKey: API_KEY, baseUrl: BASE_URL });
    await client.listTasks();

    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE_URL}/run/tasks`);
  });
});

describe("NamiFusion#subscribe", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("polls getTask pending -> processing -> completed, backing off x1.5 per poll, calling onUpdate each time, resolving with the completed task", async () => {
    vi.useFakeTimers();

    const pendingTask = makeTask({ status: "pending" });
    const processingTask = makeTask({ status: "processing" });
    const completedTask = makeTask({ status: "completed", output: { url: "https://cdn.example/x.png" } });

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, { task_uuid: "t1", status: "pending" }))
      .mockResolvedValueOnce(jsonResponse(200, pendingTask))
      .mockResolvedValueOnce(jsonResponse(200, processingTask))
      .mockResolvedValueOnce(jsonResponse(200, completedTask));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const onUpdate = vi.fn();
    const client = new NamiFusion({ apiKey: API_KEY, baseUrl: BASE_URL });
    const promise = client.subscribe("acme/model-x", { input: {}, onUpdate });

    await vi.advanceTimersByTimeAsync(2000); // first poll: default pollIntervalMs
    await vi.advanceTimersByTimeAsync(3000); // second poll: 2000 * 1.5
    await vi.advanceTimersByTimeAsync(4500); // third poll: 3000 * 1.5

    const result = await promise;

    expect(result).toEqual(completedTask);
    expect(fetchMock).toHaveBeenCalledTimes(4); // 1 run + 3 polls
    expect(onUpdate).toHaveBeenCalledTimes(3);
    expect(onUpdate.mock.calls[0][0]).toEqual(pendingTask);
    expect(onUpdate.mock.calls[1][0]).toEqual(processingTask);
    expect(onUpdate.mock.calls[2][0]).toEqual(completedTask);

    // sanity-check the actual poll request shape
    const [pollUrl, pollInit] = fetchMock.mock.calls[1];
    expect(pollUrl).toBe(`${BASE_URL}/run/tasks/t1`);
    expect(pollInit.method).toBe("GET");
  });

  it("throws TaskFailedError carrying the terminal task when the task reaches status=failed", async () => {
    vi.useFakeTimers();

    const failedTask = makeTask({ status: "failed", error_message: "boom" });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, { task_uuid: "t1", status: "pending" }))
      .mockResolvedValueOnce(jsonResponse(200, failedTask));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const client = new NamiFusion({ apiKey: API_KEY, baseUrl: BASE_URL });
    const promise = client.subscribe("acme/model-x", { input: {} });
    const assertion = expect(promise).rejects.toBeInstanceOf(TaskFailedError);

    await vi.advanceTimersByTimeAsync(2000);
    await assertion;

    const err = (await promise.catch((e) => e)) as TaskFailedError;
    expect(err.task).toEqual(failedTask);
    expect(err.message).toContain("boom");
  });

  it("throws TaskFailedError when the task reaches status=cancelled", async () => {
    vi.useFakeTimers();

    const cancelledTask = makeTask({ status: "cancelled" });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, { task_uuid: "t1", status: "pending" }))
      .mockResolvedValueOnce(jsonResponse(200, cancelledTask));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const client = new NamiFusion({ apiKey: API_KEY, baseUrl: BASE_URL });
    const promise = client.subscribe("acme/model-x", { input: {} });
    const assertion = expect(promise).rejects.toBeInstanceOf(TaskFailedError);

    await vi.advanceTimersByTimeAsync(2000);
    await assertion;

    const err = (await promise.catch((e) => e)) as TaskFailedError;
    expect(err.task).toEqual(cancelledTask);
  });

  it("throws a NamiFusionError once the total subscribe timeout elapses without a terminal state", async () => {
    vi.useFakeTimers();

    const processingTask = makeTask({ status: "processing" });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, { task_uuid: "t1", status: "pending" }))
      .mockResolvedValue(jsonResponse(200, processingTask));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const client = new NamiFusion({ apiKey: API_KEY, baseUrl: BASE_URL });
    const promise = client.subscribe("acme/model-x", {
      input: {},
      pollIntervalMs: 2000,
      timeoutMs: 5000,
    });
    const assertion = expect(promise).rejects.toBeInstanceOf(NamiFusionError);

    await vi.runAllTimersAsync();
    await assertion;

    // 1 run POST + 2 polls that fit inside the 5000ms budget (waits of 2000
    // then 3000 exactly exhaust it; the 3rd wait would start at elapsed=5000
    // and is skipped in favor of throwing immediately).
    expect(fetchMock).toHaveBeenCalledTimes(3);

    const err = (await promise.catch((e) => e)) as NamiFusionError;
    expect(err).not.toBeInstanceOf(TaskFailedError);
    expect(err.message).toMatch(/timed out/i);
    expect(err.detail).toEqual({ task_uuid: "t1", timeout_ms: 5000 });
  });

  it("defaults pollIntervalMs to 2000 and subscribe timeoutMs to 1_800_000 when omitted", async () => {
    vi.useFakeTimers();

    const completedTask = makeTask({ status: "completed" });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, { task_uuid: "t1", status: "pending" }))
      .mockResolvedValueOnce(jsonResponse(200, completedTask));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const client = new NamiFusion({ apiKey: API_KEY, baseUrl: BASE_URL });
    const promise = client.subscribe("acme/model-x", { input: {} });

    // just under the default 2000ms poll interval: no poll yet
    await vi.advanceTimersByTimeAsync(1999);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1);
    const result = await promise;
    expect(result).toEqual(completedTask);
  });

  it("stops polling and rejects with signal.reason when aborted mid-wait", async () => {
    vi.useFakeTimers();

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, { task_uuid: "t1", status: "pending" }))
      .mockResolvedValue(jsonResponse(200, makeTask({ status: "processing" })));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const controller = new AbortController();
    const abortReason = new Error("caller cancelled");
    const client = new NamiFusion({ apiKey: API_KEY, baseUrl: BASE_URL });
    const promise = client.subscribe("acme/model-x", { input: {}, signal: controller.signal });
    const assertion = expect(promise).rejects.toBe(abortReason);

    await vi.advanceTimersByTimeAsync(500); // still inside the first 2000ms wait
    controller.abort(abortReason);
    await assertion;

    // only the initial run() POST happened — no poll was ever issued
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("rejects with signal.reason and does not call onUpdate when aborted while a getTask poll is in flight", async () => {
    vi.useFakeTimers();

    let resolveGetTaskFetch!: (response: Response) => void;
    const getTaskFetchPromise = new Promise<Response>((resolve) => {
      resolveGetTaskFetch = resolve;
    });

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, { task_uuid: "t1", status: "pending" }))
      .mockImplementationOnce(() => getTaskFetchPromise);
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const controller = new AbortController();
    const abortReason = new Error("caller cancelled");
    const onUpdate = vi.fn();
    const client = new NamiFusion({ apiKey: API_KEY, baseUrl: BASE_URL });
    const promise = client.subscribe("acme/model-x", {
      input: {},
      onUpdate,
      signal: controller.signal,
    });
    const assertion = expect(promise).rejects.toBe(abortReason);

    // advance past the first poll's sleep so the getTask() fetch is issued and in flight
    await vi.advanceTimersByTimeAsync(2000);
    expect(fetchMock).toHaveBeenCalledTimes(2); // run POST + in-flight getTask GET

    // abort while that getTask HTTP request is still pending (unresolved)
    controller.abort(abortReason);

    // now let the in-flight getTask response resolve — it should still complete normally,
    // but the post-getTask abort check must reject before onUpdate is invoked
    resolveGetTaskFetch(jsonResponse(200, makeTask({ status: "processing" })));

    await assertion;

    expect(onUpdate).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(2); // no further polls after the abort
  });
});

describe("toDataUrl", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("encodes a Uint8Array as a base64 data URL", async () => {
    const bytes = new Uint8Array([72, 101, 108, 108, 111]); // "Hello"
    const url = await toDataUrl(bytes, "text/plain");
    expect(url).toBe("data:text/plain;base64,SGVsbG8=");
  });

  it("encodes an ArrayBuffer", async () => {
    const bytes = new Uint8Array([1, 2, 3, 4]);
    const url = await toDataUrl(bytes.buffer, "application/octet-stream");
    expect(url).toBe(`data:application/octet-stream;base64,${Buffer.from(bytes).toString("base64")}`);
  });

  it("encodes a Blob", async () => {
    const blob = new Blob([new Uint8Array([72, 105])], { type: "text/plain" }); // "Hi"
    const url = await toDataUrl(blob, "image/png");
    expect(url).toBe("data:image/png;base64,SGk=");
  });

  it("falls back to the chunked btoa path when Buffer is unavailable (browser-like global)", async () => {
    vi.stubGlobal("Buffer", undefined);
    const bytes = new Uint8Array([72, 101, 108, 108, 111]);
    const url = await toDataUrl(bytes, "text/plain");
    expect(url).toBe("data:text/plain;base64,SGVsbG8=");
  });
});
