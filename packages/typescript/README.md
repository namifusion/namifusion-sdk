# @namifusion/client

Official TypeScript/JavaScript SDK for the [NamiFusion](https://www.namifusion.com) AI model marketplace API.

Requires Node.js >= 18 (uses the global `fetch` and `crypto.randomUUID`).

## Install

```sh
npm install @namifusion/client
```

## Quick start

```ts
import { NamiFusion } from "@namifusion/client";

// Reads the API key from the NAMIFUSION_API_KEY environment variable.
// Pass { apiKey: "sk-..." } explicitly if you'd rather not use env vars.
const client = new NamiFusion();

const task = await client.subscribe("google/nano-banana-pro/text-to-image", {
  input: { prompt: "a cat wearing sunglasses" },
});

console.log(task.status, task.output);
```

`subscribe()` submits the run and polls `getTask()` until the task reaches a
terminal state, resolving with the completed `Task`. It throws
`TaskFailedError` if the task ends up `failed` or `cancelled`.

## Submitting without waiting (`run` + webhook)

The API is always async — `run()` returns immediately with the task's initial
state instead of waiting for completion. Pass `webhookUrl` if you'd rather be
notified than poll:

```ts
const submitted = await client.run("google/nano-banana-pro/text-to-image", {
  input: { prompt: "a cat wearing sunglasses" },
  webhookUrl: "https://example.com/webhooks/namifusion",
});

console.log(submitted.task_uuid, submitted.status);
```

The webhook receives the same JSON shape as `getTask()`'s return value
(`Task`). There's no signature to verify, no retry on delivery failure, and
the server gives up waiting on your endpoint after 10 seconds — treat it as a
best-effort notification, not a guaranteed delivery.

## Checking a task manually (`getTask`)

```ts
const task = await client.getTask(submitted.task_uuid);
console.log(task.status, task.output);
```

## Listing tasks (`listTasks`)

```ts
const page = await client.listTasks({ limit: 20, status: "completed" });
for (const task of page.items) {
  console.log(task.task_uuid, task.status);
}
```

`listTasks` returns `{ total, items }`. Optional params: `skip`, `limit`,
`modelId`, `status`.

## Output URLs expire in ~7 days

`task.output` fields that point at generated files are unsigned COS CDN
links with roughly a 7-day lifetime. Download or persist anything you need
to keep — don't hold onto the URL long-term.

## File input (`toDataUrl`)

There's no dedicated upload endpoint. Models that accept file input expose a
field (check the model's input schema) that accepts a base64 string or a
`data:` URL — the server auto-uploads it to COS on the fly once the payload
is at least ~10KB. `toDataUrl()` builds that value from raw bytes:

```ts
import { NamiFusion, toDataUrl } from "@namifusion/client";
import { readFile } from "node:fs/promises";

const client = new NamiFusion();

const bytes = await readFile("./photo.png");
const dataUrl = await toDataUrl(bytes, "image/png");

const task = await client.subscribe("some/model", {
  input: { image: dataUrl },
});
```

`toDataUrl(data, mimeType)` accepts `Uint8Array | ArrayBuffer | Blob` and
returns `Promise<string>`.

## Errors

Every error the SDK throws extends `NamiFusionError` (`message`, `status`,
`code?`, `detail?`):

| Class | HTTP status | Thrown when |
| --- | --- | --- |
| `AuthenticationError` | 401 | API key missing or invalid — also thrown by the constructor itself when no `apiKey` and no `NAMIFUSION_API_KEY` env var are available |
| `InsufficientCreditsError` | 402 | Account doesn't have enough credits for this request |
| `ForbiddenError` | 403 | Authenticated but not authorized for this resource |
| `NotFoundError` | 404 | `model_id` or `task_uuid` not found |
| `InvalidRequestError` | 400 / 422 | Request body failed validation |
| `RateLimitError` | 429 | Per-second rate limit (carries `retryAfter` in seconds) or monthly quota exceeded (`retryAfter` is `undefined` in that case) |
| `ServerError` | 5xx | Server-side failure |
| `TaskFailedError` | — (`status` is `0`) | `subscribe()`'s task reached `failed`/`cancelled`; carries the terminal `.task` |

```ts
import { NamiFusionError, TaskFailedError } from "@namifusion/client";

try {
  await client.subscribe(modelId, { input });
} catch (err) {
  if (err instanceof TaskFailedError) {
    console.error("task failed:", err.task.error_message);
  } else if (err instanceof NamiFusionError) {
    console.error(err.status, err.message);
  } else {
    throw err;
  }
}
```

## Retries & idempotency

Network errors, 429 responses (honoring `Retry-After` when the server sends
one), and 502/503/504 responses are retried automatically, up to
`maxRetries` (default 2) times with exponential backoff. Other 4xx responses
are never retried.

`run()` — including the one `subscribe()` calls internally — always sends an
`Idempotency-Key` header: either the `idempotencyKey` you pass, or an
auto-generated UUID that's reused across the SDK's own retry attempts of
that call. The server maps the same account + key to the task created by the
*first* call and returns that task instead of creating a new one, without
charging again. That makes retrying a run — automatic or your own — safe
from duplicate billing.

## Configuration

| Option | Env var | Default |
| --- | --- | --- |
| `apiKey` | `NAMIFUSION_API_KEY` | required — the constructor throws `AuthenticationError` if both are missing |
| `baseUrl` | — | `https://www.namifusion.com/api/v1/marketplace` |
| `maxRetries` | — | `2` |
| `timeoutMs` | — | `60000` (per-request timeout, in ms) |

```ts
const client = new NamiFusion({
  apiKey: "sk-...",
  baseUrl: "https://test.namifusion.com/api/v1/marketplace", // e.g. to hit the test environment
  maxRetries: 3,
  timeoutMs: 30_000,
});
```

`subscribe()` additionally takes `pollIntervalMs` (default `2000`, backed
off x1.5 per poll, capped at `10000`), a total `timeoutMs` (default
`1_800_000` — 30 minutes), an `onUpdate` callback fired once per poll, and an
`AbortSignal`.

## License

MIT — see [LICENSE](./LICENSE).
