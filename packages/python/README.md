# namifusion

Official Python SDK for the [NamiFusion](https://www.namifusion.com) AI model marketplace API.

Requires Python >= 3.9. Depends only on `httpx`.

## Install

```sh
pip install namifusion
```

## Quick start

```python
from namifusion import NamiFusion

# Reads the API key from the NAMIFUSION_API_KEY environment variable.
# Pass NamiFusion(api_key="sk-...") explicitly if you'd rather not use env vars.
client = NamiFusion()

task = client.subscribe(
    "google/nano-banana-pro/text-to-image",
    input={"prompt": "a cat wearing sunglasses"},
)

print(task.status, task.output)
```

`subscribe()` submits the run and polls `get_task()` until the task reaches a
terminal state, returning the completed `Task`. It raises `TaskFailedError`
if the task ends up `failed` or `cancelled`. `Task`, `RunResult`, and
`ListTasksResult` are dataclasses — always use attribute access
(`task.output`, `task.status`), not dict subscripting.

## Async client

`AsyncNamiFusion` exposes the exact same methods as `NamiFusion`, all `async`:

```python
import asyncio
from namifusion import AsyncNamiFusion

async def main():
    client = AsyncNamiFusion()
    task = await client.subscribe(
        "google/nano-banana-pro/text-to-image",
        input={"prompt": "a cat wearing sunglasses"},
    )
    print(task.status, task.output)

asyncio.run(main())
```

## Submitting without waiting (`run` + webhook)

The API is always async — `run()` returns immediately with the task's
initial state instead of waiting for completion. Pass `webhook_url` if you'd
rather be notified than poll:

```python
submitted = client.run(
    "google/nano-banana-pro/text-to-image",
    input={"prompt": "a cat wearing sunglasses"},
    webhook_url="https://example.com/webhooks/namifusion",
)

print(submitted.task_uuid, submitted.status)
```

The webhook receives the same JSON shape as `get_task()`'s return value
(`Task`). There's no signature to verify, no retry on delivery failure, and
the server gives up waiting on your endpoint after 10 seconds — treat it as a
best-effort notification, not a guaranteed delivery.

## Checking a task manually (`get_task`)

```python
task = client.get_task(submitted.task_uuid)
print(task.status, task.output)
```

## Listing tasks (`list_tasks`)

```python
page = client.list_tasks(limit=20, status="completed")
for task in page.items:
    print(task.task_uuid, task.status)
```

`list_tasks` returns a `ListTasksResult` (`.total`, `.items`). Optional
keyword params: `skip`, `limit`, `model_id`, `status`.

## Output URLs expire in ~7 days

`task.output` fields that point at generated files are unsigned COS CDN
links with roughly a 7-day lifetime. Download or persist anything you need
to keep — don't hold onto the URL long-term.

## File input (`to_data_url`)

There's no dedicated upload endpoint. Models that accept file input expose a
field (check the model's input schema) that accepts a base64 string or a
`data:` URL — the server auto-uploads it to COS on the fly once the payload
is at least ~10KB. `to_data_url()` builds that value from raw bytes:

```python
from namifusion import NamiFusion, to_data_url

client = NamiFusion()

with open("photo.png", "rb") as f:
    data_url = to_data_url(f.read(), "image/png")

task = client.subscribe("some/model", input={"image": data_url})
```

`to_data_url(data: bytes, mime_type: str) -> str` is synchronous (no
`await`).

## Errors

Every error the SDK raises extends `NamiFusionError` (`message`, `status`,
`code`, `detail`):

| Class | HTTP status | Raised when |
| --- | --- | --- |
| `AuthenticationError` | 401 | API key missing or invalid — also raised by the constructor itself when no `api_key` and no `NAMIFUSION_API_KEY` env var are available |
| `InsufficientCreditsError` | 402 | Account doesn't have enough credits for this request |
| `ForbiddenError` | 403 | Authenticated but not authorized for this resource |
| `NotFoundError` | 404 | `model_id` or `task_uuid` not found |
| `InvalidRequestError` | 400 / 422 | Request body failed validation |
| `RateLimitError` | 429 | Per-second rate limit (carries `retry_after` in seconds) or monthly quota exceeded (`retry_after` is `None` in that case) |
| `ServerError` | 5xx | Server-side failure |
| `TaskFailedError` | — (`status` is `0`) | `subscribe()`'s task reached `failed`/`cancelled`; carries the terminal `.task` |

```python
from namifusion import NamiFusionError, TaskFailedError

try:
    task = client.subscribe(model_id, input=input_data)
except TaskFailedError as err:
    print("task failed:", err.task.error_message)
except NamiFusionError as err:
    print(err.status, err.message)
```

## Retries & idempotency

Network errors, 429 responses (honoring `Retry-After` when the server sends
one), and 502/503/504 responses are retried automatically, up to
`max_retries` (default 2) times with exponential backoff. Other 4xx
responses are never retried.

`run()` — including the one `subscribe()` calls internally — always sends an
`Idempotency-Key` header: either the `idempotency_key` you pass, or an
auto-generated UUID that's reused across the SDK's own retry attempts of
that call. The server maps the same account + key to the task created by the
*first* call and returns that task instead of creating a new one, without
charging again. That makes retrying a run — automatic or your own — safe
from duplicate billing.

## Configuration

| Parameter | Env var | Default |
| --- | --- | --- |
| `api_key` | `NAMIFUSION_API_KEY` | required — the constructor raises `AuthenticationError` if both are missing |
| `base_url` | — | `https://www.namifusion.com/api/v1/marketplace` |
| `max_retries` | — | `2` |
| `timeout` | — | `60.0` (per-request timeout, in seconds) |

```python
client = NamiFusion(
    api_key="sk-...",
    base_url="https://test.namifusion.com/api/v1/marketplace",  # e.g. to hit the test environment
    max_retries=3,
    timeout=30.0,
)
```

`subscribe()` additionally takes `poll_interval` (default `2.0`s, backed off
x1.5 per poll, capped at `10.0`s), a total `timeout` (default `1800.0`s — 30
minutes), and an `on_update` callback fired once per poll.

## License

MIT — see [LICENSE](./LICENSE).
