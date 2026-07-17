"""HTTP transport + retry logic.

Mirrors packages/typescript/src/http.ts's `request()`: retries network
errors and 429/502/503/504 responses with exponential backoff (base
0.5s, doubling per attempt, capped at 8s, +-20% jitter), except a 429
carrying a `Retry-After` header waits that many seconds (capped at 30s,
via `_errors.parse_retry_after_seconds`) instead. Any other 4xx raises
immediately via `error_from_response` without retrying.

`max_retries` counts retries *in addition to* the initial attempt (the
contract's "max_retries=2 -> at most 3 requests" semantics, matching the
TS SDK).

This module has no `signal`/cancellation concept — the TS side's
`AbortSignal` doesn't have a Python equivalent here by design (see the
Task 4 brief): the sync path is bounded by the per-attempt `timeout`
passed to httpx, and the async path lets `asyncio.CancelledError`
propagate naturally through `await`s (an external `asyncio.Task.cancel()`
interrupts an in-flight request or a pending backoff sleep the same way
it would interrupt any other awaited coroutine).

Two entry points share the same pure retry-decision helpers
(`_should_retry`, `_backoff_delay`) so sync and async behavior can't
drift apart:

- `request()` — sync, backed by `httpx.Client`.
- `arequest()` — async, backed by `httpx.AsyncClient`.

Both accept an injectable `sleep`/async `sleep` callable so tests never
actually wait out a backoff, and an injectable `transport` so tests run
against `httpx.MockTransport` instead of the network.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Tuple

import httpx

from ._errors import error_from_response, parse_retry_after_seconds

_RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})


@dataclass
class RequestOptions:
    """Options accepted by `request()`/`arequest()`. This is the SDK's
    internal transport primitive — `client.py` (Task 5) is the only
    intended caller.
    """

    method: str
    url: str
    api_key: str
    #: Per-attempt timeout in seconds. Each retry gets a fresh timeout
    #: window (httpx applies `timeout` per call, not per Client lifetime).
    timeout: float
    #: Number of retries *in addition to* the initial attempt.
    max_retries: int
    user_agent: str
    body: Optional[Any] = None
    headers: Optional[Mapping[str, str]] = None


def _should_retry(status: int) -> bool:
    return status in _RETRYABLE_STATUSES


def _backoff_delay(
    attempt: int,
    retry_after: Optional[float] = None,
    *,
    random_fn: Callable[[], float] = random.random,
) -> float:
    """Computes the delay (seconds) before retry attempt `attempt`
    (0-indexed: 0 = delay before the 1st retry).

    When `retry_after` is given (a 429's parsed, already-capped-at-30s
    Retry-After value) it's used verbatim, no jitter applied. Otherwise:
    base 0.5s, doubling per attempt, capped at 8s, with up to +-20%
    jitter so concurrent clients don't retry in lockstep.

    `random_fn` is injectable (defaults to `random.random`) so tests can
    assert exact values instead of ranges.
    """
    if retry_after is not None:
        return retry_after
    base = min(0.5 * (2**attempt), 8.0)
    jitter_factor = 1 + (random_fn() * 0.4 - 0.2)  # [0.8, 1.2]
    return max(0.0, base * jitter_factor)


def _has_header(headers: Dict[str, str], name: str) -> bool:
    name_lower = name.lower()
    return any(key.lower() == name_lower for key in headers)


def _prepare(opts: RequestOptions) -> Tuple[Optional[bytes], Dict[str, str]]:
    has_body = opts.body is not None
    content = json.dumps(opts.body).encode("utf-8") if has_body else None

    headers = dict(opts.headers or {})
    headers["Authorization"] = f"Bearer {opts.api_key}"
    headers["User-Agent"] = opts.user_agent
    if has_body and not _has_header(headers, "Content-Type"):
        headers["Content-Type"] = "application/json"

    return content, headers


def _read_json(response: httpx.Response) -> Any:
    text = response.text
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        # Non-JSON body (shouldn't happen against this API, but don't
        # crash on it).
        return text


def request(
    opts: RequestOptions,
    *,
    transport: Optional[httpx.BaseTransport] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Performs a single logical HTTP request with retry handling
    (sync, `httpx.Client`). See module docstring for retry semantics.
    """
    content, headers = _prepare(opts)
    attempt = 0

    with httpx.Client(transport=transport) as client:
        while True:
            try:
                response = client.request(
                    opts.method,
                    opts.url,
                    content=content,
                    headers=headers,
                    timeout=opts.timeout,
                )
            except httpx.RequestError:
                if attempt >= opts.max_retries:
                    raise
                sleep(_backoff_delay(attempt))
                attempt += 1
                continue

            if response.is_success:
                return _read_json(response)

            parsed_body = _read_json(response)

            if _should_retry(response.status_code) and attempt < opts.max_retries:
                retry_after = (
                    parse_retry_after_seconds(response.headers)
                    if response.status_code == 429
                    else None
                )
                sleep(_backoff_delay(attempt, retry_after))
                attempt += 1
                continue

            raise error_from_response(response.status_code, parsed_body, response.headers)


async def arequest(
    opts: RequestOptions,
    *,
    transport: Optional[httpx.BaseTransport] = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Any:
    """Performs a single logical HTTP request with retry handling
    (async, `httpx.AsyncClient`). See module docstring for retry
    semantics.
    """
    content, headers = _prepare(opts)
    attempt = 0

    async with httpx.AsyncClient(transport=transport) as client:
        while True:
            try:
                response = await client.request(
                    opts.method,
                    opts.url,
                    content=content,
                    headers=headers,
                    timeout=opts.timeout,
                )
            except httpx.RequestError:
                if attempt >= opts.max_retries:
                    raise
                await sleep(_backoff_delay(attempt))
                attempt += 1
                continue

            if response.is_success:
                return _read_json(response)

            parsed_body = _read_json(response)

            if _should_retry(response.status_code) and attempt < opts.max_retries:
                retry_after = (
                    parse_retry_after_seconds(response.headers)
                    if response.status_code == 429
                    else None
                )
                await sleep(_backoff_delay(attempt, retry_after))
                attempt += 1
                continue

            raise error_from_response(response.status_code, parsed_body, response.headers)
