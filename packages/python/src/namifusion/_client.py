"""NamiFusion / AsyncNamiFusion — the SDK's public client classes.

Mirrors packages/typescript/src/client.ts's `NamiFusion` and the
cross-repo shared contract's "SDK 公共 API 契约" Python section
(docs/superpowers/plans/2026-07-17-sdk-contract.md). `NamiFusion` (sync)
and `AsyncNamiFusion` (async) expose the same five methods —
`run`/`get_task`/`list_tasks`/`subscribe`, plus the shared constructor —
built on top of `_transport.request`/`arequest`.

To keep the sync and async classes from drifting apart, all the
*decision* logic (URL building, request-body shaping, polling backoff,
terminal-state detection, error construction) lives in module-level pure
functions below and is called identically by both classes. The classes
themselves are thin — the only difference between them is `await` and
which `_transport` entry point / default sleep implementation they use.

Two private, leading-underscore constructor kwargs exist purely as test
seams (not part of the public contract's `NamiFusion(api_key=None,
base_url=None, max_retries=2, timeout=60.0)` signature):

- `_transport`: an `httpx.BaseTransport` forwarded to every
  `_transport.request`/`arequest` call, letting tests run against
  `httpx.MockTransport` instead of the network (same technique
  `tests/test_transport.py` uses directly against `_transport`).
- `_sleep` / `_now`: injectable sleep + monotonic-clock callables so
  `subscribe()`'s polling loop (and the retry backoff inside
  `_transport`) never actually wait in tests.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional
from urllib.parse import urlencode

import httpx

from . import _transport as _transport_module
from ._errors import AuthenticationError, NamiFusionError, TaskFailedError
from ._transport import RequestOptions
from ._types import ListTasksResult, RunResult, Task

DEFAULT_BASE_URL = "https://www.namifusion.com/api/v1/marketplace"
DEFAULT_MAX_RETRIES = 2
DEFAULT_TIMEOUT = 60.0

DEFAULT_POLL_INTERVAL = 2.0
POLL_INTERVAL_BACKOFF_FACTOR = 1.5
POLL_INTERVAL_CAP = 10.0
#: 30 minutes — a wavespeed-python-style lenient default; a tighter
#: default (e.g. 10 minutes) would spuriously time out slower video
#: models. Matches client.ts's DEFAULT_SUBSCRIBE_TIMEOUT_MS.
DEFAULT_SUBSCRIBE_TIMEOUT = 1800.0

_SDK_VERSION = "0.1.0"
_USER_AGENT = f"namifusion-python/{_SDK_VERSION}"

_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


# --- shared pure decision functions (sync + async call these identically) ---


def _resolve_api_key(api_key: Optional[str]) -> str:
    # `is not None` (not a truthiness check) so an explicitly-passed
    # empty string doesn't silently fall back to the env var — it falls
    # through to the same "missing" error as omitting api_key entirely,
    # matching client.ts's `options.apiKey ?? apiKeyFromEnv()` (`??` only
    # treats null/undefined as absent).
    resolved = api_key if api_key is not None else os.environ.get("NAMIFUSION_API_KEY")
    if not resolved:
        raise AuthenticationError(
            "Missing API key: pass `api_key` to the NamiFusion constructor, "
            "or set the NAMIFUSION_API_KEY environment variable"
        )
    return resolved


def _resolve_base_url(base_url: Optional[str]) -> str:
    return (base_url or DEFAULT_BASE_URL).rstrip("/")


def _run_url(base_url: str, model_id: str) -> str:
    # model_id is concatenated into the path as-is (e.g.
    # "google/nano-banana-pro/text-to-image") — its embedded slashes are
    # intentionally not percent-encoded, matching the server's `:path`
    # route parameter (see client.ts's `run()` docstring).
    return f"{base_url}/run/{model_id}"


def _task_url(base_url: str, task_uuid: str) -> str:
    return f"{base_url}/run/tasks/{task_uuid}"


def _list_tasks_url(
    base_url: str,
    skip: Optional[int],
    limit: Optional[int],
    model_id: Optional[str],
    status: Optional[str],
) -> str:
    params: Dict[str, Any] = {}
    if skip is not None:
        params["skip"] = skip
    if limit is not None:
        params["limit"] = limit
    if model_id is not None:
        params["model_id"] = model_id
    if status is not None:
        params["status"] = status
    qs = urlencode(params)
    return f"{base_url}/run/tasks" + (f"?{qs}" if qs else "")


def _run_body(input: Mapping[str, Any], webhook_url: Optional[str]) -> Dict[str, Any]:
    body: Dict[str, Any] = {"input": input}
    if webhook_url is not None:
        body["webhook_url"] = webhook_url
    return body


def _resolve_idempotency_key(idempotency_key: Optional[str]) -> str:
    # Generated fresh per call (never cached on the client) so two
    # independent run() calls get different keys; _transport builds the
    # headers dict once per logical request and reuses it across
    # internal retry attempts, so a single call's key is retry-safe.
    # `is not None` so an explicitly-passed empty string is sent
    # verbatim rather than silently replaced (matches client.ts's `??`).
    return idempotency_key if idempotency_key is not None else str(uuid.uuid4())


def _is_terminal(status: str) -> bool:
    return status in _TERMINAL_STATUSES


def _next_poll_interval(interval: float) -> float:
    return min(interval * POLL_INTERVAL_BACKOFF_FACTOR, POLL_INTERVAL_CAP)


def _sleep_duration(interval: float, elapsed: float, timeout: float) -> float:
    return min(interval, timeout - elapsed)


#: Floor (seconds) for a converged per-request timeout — mirrors client.ts's
#: 1ms floor (`Math.max(1, ...)`), keeping httpx from getting a zero/negative
#: timeout once the subscribe deadline is spent.
_MIN_REQUEST_TIMEOUT = 0.001


def _request_timeout(client_timeout: float, deadline: float, now: float) -> float:
    """Converges a single request's timeout to
    max(1ms, min(client timeout, remaining budget)) so subscribe()'s total
    timeout acts as a hard deadline — a hung run()/get_task() can't block
    past it. Mirrors client.ts's `perRequestTimeoutMs()`.
    """
    return max(_MIN_REQUEST_TIMEOUT, min(client_timeout, deadline - now))


def _subscribe_timeout_error(task_uuid: str, timeout: float) -> NamiFusionError:
    return NamiFusionError(
        f"subscribe() timed out after {timeout}s waiting for task {task_uuid} "
        "to reach a terminal state",
        detail={"task_uuid": task_uuid, "timeout": timeout},
    )


def _task_failed_error(task: Task) -> TaskFailedError:
    message = task.error_message or f"Task {task.task_uuid} {task.status}"
    return TaskFailedError(message, task)


class NamiFusion:
    """Sync client for the NamiFusion AI model marketplace API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: float = DEFAULT_TIMEOUT,
        *,
        _transport: Optional[httpx.BaseTransport] = None,
        _sleep: Optional[Callable[[float], None]] = None,
        _now: Optional[Callable[[], float]] = None,
    ) -> None:
        self._api_key = _resolve_api_key(api_key)
        self._base_url = _resolve_base_url(base_url)
        self._max_retries = max_retries
        self._timeout = timeout
        self._http_transport = _transport
        self._sleep = _sleep or time.sleep
        self._now = _now or time.monotonic

    def _request(self, opts: RequestOptions) -> Any:
        return _transport_module.request(
            opts, transport=self._http_transport, sleep=self._sleep
        )

    def run(
        self,
        model_id: str,
        *,
        input: Mapping[str, Any],
        webhook_url: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        _timeout: Optional[float] = None,
    ) -> RunResult:
        # `_timeout` is an internal test/subscribe seam (not in the public
        # contract signature): subscribe() passes its hard-deadline-converged
        # per-request budget here so a hung submit can't overrun the total.
        key = _resolve_idempotency_key(idempotency_key)
        opts = RequestOptions(
            method="POST",
            url=_run_url(self._base_url, model_id),
            api_key=self._api_key,
            timeout=_timeout if _timeout is not None else self._timeout,
            max_retries=self._max_retries,
            user_agent=_USER_AGENT,
            body=_run_body(input, webhook_url),
            headers={"Idempotency-Key": key},
        )
        data = self._request(opts)
        return RunResult.from_dict(data)

    def get_task(self, task_uuid: str, *, _timeout: Optional[float] = None) -> Task:
        opts = RequestOptions(
            method="GET",
            url=_task_url(self._base_url, task_uuid),
            api_key=self._api_key,
            timeout=_timeout if _timeout is not None else self._timeout,
            max_retries=self._max_retries,
            user_agent=_USER_AGENT,
        )
        data = self._request(opts)
        return Task.from_dict(data)

    def list_tasks(
        self,
        *,
        skip: Optional[int] = None,
        limit: Optional[int] = None,
        model_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> ListTasksResult:
        opts = RequestOptions(
            method="GET",
            url=_list_tasks_url(self._base_url, skip, limit, model_id, status),
            api_key=self._api_key,
            timeout=self._timeout,
            max_retries=self._max_retries,
            user_agent=_USER_AGENT,
        )
        data = self._request(opts)
        return ListTasksResult.from_dict(data)

    def subscribe(
        self,
        model_id: str,
        *,
        input: Mapping[str, Any],
        webhook_url: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        timeout: float = DEFAULT_SUBSCRIBE_TIMEOUT,
        on_update: Optional[Callable[[Task], None]] = None,
    ) -> Task:
        # Hard deadline: the clock starts *before* the submit, so the whole
        # call — first run() included — is bounded by `timeout`. Each internal
        # request's own timeout is converged to the remaining budget so a hung
        # run()/get_task() can't block past the deadline (the elapsed check
        # below only fires between requests, not during one).
        start = self._now()
        deadline = start + timeout
        submitted = self.run(
            model_id,
            input=input,
            webhook_url=webhook_url,
            idempotency_key=idempotency_key,
            _timeout=_request_timeout(self._timeout, deadline, self._now()),
        )
        interval = poll_interval

        while True:
            elapsed = self._now() - start
            if elapsed >= timeout:
                raise _subscribe_timeout_error(submitted.task_uuid, timeout)

            self._sleep(_sleep_duration(interval, elapsed, timeout))

            task = self.get_task(
                submitted.task_uuid,
                _timeout=_request_timeout(self._timeout, deadline, self._now()),
            )
            if on_update is not None:
                on_update(task)

            if task.status == "completed":
                return task
            if _is_terminal(task.status):
                raise _task_failed_error(task)

            interval = _next_poll_interval(interval)


class AsyncNamiFusion:
    """Async client for the NamiFusion AI model marketplace API. Same
    surface as `NamiFusion`, all methods `async`/`await`.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: float = DEFAULT_TIMEOUT,
        *,
        _transport: Optional[httpx.BaseTransport] = None,
        _sleep: Optional[Callable[[float], Awaitable[None]]] = None,
        _now: Optional[Callable[[], float]] = None,
    ) -> None:
        self._api_key = _resolve_api_key(api_key)
        self._base_url = _resolve_base_url(base_url)
        self._max_retries = max_retries
        self._timeout = timeout
        self._http_transport = _transport
        self._sleep = _sleep or asyncio.sleep
        self._now = _now or time.monotonic

    async def _request(self, opts: RequestOptions) -> Any:
        return await _transport_module.arequest(
            opts, transport=self._http_transport, sleep=self._sleep
        )

    async def run(
        self,
        model_id: str,
        *,
        input: Mapping[str, Any],
        webhook_url: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        _timeout: Optional[float] = None,
    ) -> RunResult:
        # `_timeout` is an internal test/subscribe seam (not in the public
        # contract signature): subscribe() passes its hard-deadline-converged
        # per-request budget here so a hung submit can't overrun the total.
        key = _resolve_idempotency_key(idempotency_key)
        opts = RequestOptions(
            method="POST",
            url=_run_url(self._base_url, model_id),
            api_key=self._api_key,
            timeout=_timeout if _timeout is not None else self._timeout,
            max_retries=self._max_retries,
            user_agent=_USER_AGENT,
            body=_run_body(input, webhook_url),
            headers={"Idempotency-Key": key},
        )
        data = await self._request(opts)
        return RunResult.from_dict(data)

    async def get_task(self, task_uuid: str, *, _timeout: Optional[float] = None) -> Task:
        opts = RequestOptions(
            method="GET",
            url=_task_url(self._base_url, task_uuid),
            api_key=self._api_key,
            timeout=_timeout if _timeout is not None else self._timeout,
            max_retries=self._max_retries,
            user_agent=_USER_AGENT,
        )
        data = await self._request(opts)
        return Task.from_dict(data)

    async def list_tasks(
        self,
        *,
        skip: Optional[int] = None,
        limit: Optional[int] = None,
        model_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> ListTasksResult:
        opts = RequestOptions(
            method="GET",
            url=_list_tasks_url(self._base_url, skip, limit, model_id, status),
            api_key=self._api_key,
            timeout=self._timeout,
            max_retries=self._max_retries,
            user_agent=_USER_AGENT,
        )
        data = await self._request(opts)
        return ListTasksResult.from_dict(data)

    async def subscribe(
        self,
        model_id: str,
        *,
        input: Mapping[str, Any],
        webhook_url: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        timeout: float = DEFAULT_SUBSCRIBE_TIMEOUT,
        on_update: Optional[Callable[[Task], None]] = None,
    ) -> Task:
        # Hard deadline: the clock starts *before* the submit, so the whole
        # call — first run() included — is bounded by `timeout`. Each internal
        # request's own timeout is converged to the remaining budget so a hung
        # run()/get_task() can't block past the deadline (the elapsed check
        # below only fires between requests, not during one).
        start = self._now()
        deadline = start + timeout
        submitted = await self.run(
            model_id,
            input=input,
            webhook_url=webhook_url,
            idempotency_key=idempotency_key,
            _timeout=_request_timeout(self._timeout, deadline, self._now()),
        )
        interval = poll_interval

        while True:
            elapsed = self._now() - start
            if elapsed >= timeout:
                raise _subscribe_timeout_error(submitted.task_uuid, timeout)

            # No except-clause wraps this await: asyncio.CancelledError
            # firing here (an external Task.cancel() while suspended)
            # propagates straight out, same for the get_task() await
            # below.
            await self._sleep(_sleep_duration(interval, elapsed, timeout))

            task = await self.get_task(
                submitted.task_uuid,
                _timeout=_request_timeout(self._timeout, deadline, self._now()),
            )
            if on_update is not None:
                on_update(task)

            if task.status == "completed":
                return task
            if _is_terminal(task.status):
                raise _task_failed_error(task)

            interval = _next_poll_interval(interval)
