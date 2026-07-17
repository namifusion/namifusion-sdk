"""Tests for namifusion._client.AsyncNamiFusion — the async mirror of
test_client.py's NamiFusion coverage, plus the one genuinely async-only
semantic: that asyncio.CancelledError raised while subscribe() is
awaiting (a poll sleep or an in-flight get_task) propagates out of the
call unmodified, rather than being swallowed by an overly broad
except-clause somewhere in the polling loop.

Shares the request-shape assertions (run/get_task/list_tasks URL and
body building, idempotency-key generation/reuse) with test_client.py at
a lighter level of duplication — those pure decision functions
(`_client._run_url`, `_run_body`, `_list_tasks_url`, etc.) are exercised
directly by the sync suite; here we mainly confirm the async class wires
them the same way and that the polling loop's await points behave.
"""

from __future__ import annotations

import asyncio
import json
import re

import httpx
import pytest

from namifusion import NamiFusionError, Task, TaskFailedError
from namifusion._client import AsyncNamiFusion

BASE_URL = "https://test.namifusion.com/api/v1/marketplace"
API_KEY = "sk-test-key"

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


class FakeAsyncClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleep_calls: list[float] = []

    def now(self) -> float:
        return self.value

    async def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.value += seconds


def json_response(status: int, body, headers=None) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers or {})


def make_task(**overrides) -> dict:
    data = {
        "task_uuid": "t1",
        "model_id": "acme/model-x",
        "status": "pending",
        "progress": None,
        "output": None,
        "cost_credits": 10,
        "meta_info": None,
        "error_message": None,
        "created_at": "2026-07-17T00:00:00Z",
        "completed_at": None,
    }
    data.update(overrides)
    return data


def sequenced_transport(responses):
    calls: list[httpx.Request] = []
    it = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return next(it)

    return httpx.MockTransport(handler), calls


class TestConstructorAsync:
    async def test_raises_authentication_error_when_both_missing(self, monkeypatch):
        monkeypatch.delenv("NAMIFUSION_API_KEY", raising=False)
        with pytest.raises(__import__("namifusion").AuthenticationError):
            AsyncNamiFusion()


class TestRunAsync:
    async def test_posts_run_model_id_raw_with_body_input(self):
        transport, calls = sequenced_transport(
            [json_response(200, {"task_uuid": "t1", "status": "pending", "cost_credits": 5})]
        )
        client = AsyncNamiFusion(api_key=API_KEY, base_url=BASE_URL, _transport=transport)

        result = await client.run("google/nano-banana-pro/text-to-image", input={"prompt": "hi"})

        assert result.task_uuid == "t1"
        assert result.cost_credits == 5
        assert str(calls[0].url) == f"{BASE_URL}/run/google/nano-banana-pro/text-to-image"
        assert json.loads(calls[0].content) == {"input": {"prompt": "hi"}}

    async def test_auto_generates_uuid_idempotency_key_when_omitted(self):
        transport, calls = sequenced_transport([json_response(200, make_task())])
        client = AsyncNamiFusion(api_key=API_KEY, base_url=BASE_URL, _transport=transport)

        await client.run("acme/model-x", input={})

        assert UUID_RE.match(calls[0].headers["idempotency-key"])

    async def test_reuses_same_idempotency_key_across_internal_retry(self):
        transport, calls = sequenced_transport(
            [
                json_response(503, {"detail": "Service unavailable"}),
                json_response(200, make_task()),
            ]
        )
        sleep_calls = []

        async def asleep(seconds):
            sleep_calls.append(seconds)

        client = AsyncNamiFusion(
            api_key=API_KEY, base_url=BASE_URL, max_retries=1, _transport=transport, _sleep=asleep
        )

        await client.run("acme/model-x", input={})

        assert len(calls) == 2
        assert calls[0].headers["idempotency-key"] == calls[1].headers["idempotency-key"]

    async def test_generates_fresh_idempotency_key_per_independent_call(self):
        transport, calls = sequenced_transport(
            [json_response(200, make_task()), json_response(200, make_task())]
        )
        client = AsyncNamiFusion(api_key=API_KEY, base_url=BASE_URL, _transport=transport)

        await client.run("acme/model-x", input={})
        await client.run("acme/model-x", input={})

        key1 = calls[0].headers["idempotency-key"]
        key2 = calls[1].headers["idempotency-key"]
        assert key1 != key2

    async def test_includes_webhook_url_only_when_provided(self):
        transport, calls = sequenced_transport(
            [json_response(200, make_task()), json_response(200, make_task())]
        )
        client = AsyncNamiFusion(api_key=API_KEY, base_url=BASE_URL, _transport=transport)

        await client.run("acme/model-x", input={"a": 1}, webhook_url="https://example.com/hook")
        assert json.loads(calls[0].content) == {
            "input": {"a": 1},
            "webhook_url": "https://example.com/hook",
        }

        await client.run("acme/model-x", input={"a": 1})
        assert json.loads(calls[1].content) == {"input": {"a": 1}}


class TestGetTaskAsync:
    async def test_gets_run_tasks_task_uuid(self):
        transport, calls = sequenced_transport([json_response(200, make_task(status="processing"))])
        client = AsyncNamiFusion(api_key=API_KEY, base_url=BASE_URL, _transport=transport)

        result = await client.get_task("t1")

        assert isinstance(result, Task)
        assert result.status == "processing"
        assert str(calls[0].url) == f"{BASE_URL}/run/tasks/t1"


class TestListTasksAsync:
    async def test_builds_query_and_converts_items(self):
        body = {"total": 1, "items": [make_task(task_uuid="t1")]}
        transport, calls = sequenced_transport([json_response(200, body)])
        client = AsyncNamiFusion(api_key=API_KEY, base_url=BASE_URL, _transport=transport)

        result = await client.list_tasks(skip=1, limit=2)

        assert str(calls[0].url) == f"{BASE_URL}/run/tasks?skip=1&limit=2"
        assert result.total == 1
        assert isinstance(result.items[0], Task)


class TestSubscribeAsync:
    async def test_polls_pending_processing_completed_and_calls_on_update(self):
        pending = make_task(status="pending")
        processing = make_task(status="processing")
        completed = make_task(status="completed")

        transport, calls = sequenced_transport(
            [
                json_response(200, {"task_uuid": "t1", "status": "pending"}),
                json_response(200, pending),
                json_response(200, processing),
                json_response(200, completed),
            ]
        )
        clock = FakeAsyncClock()
        updates = []
        client = AsyncNamiFusion(
            api_key=API_KEY, base_url=BASE_URL, _transport=transport, _sleep=clock.sleep, _now=clock.now
        )

        result = await client.subscribe("acme/model-x", input={}, on_update=updates.append)

        assert result.status == "completed"
        assert len(calls) == 4
        assert [u.status for u in updates] == ["pending", "processing", "completed"]
        assert clock.sleep_calls == [2.0, 3.0, 4.5]

    async def test_raises_task_failed_error_on_failed(self):
        failed = make_task(status="failed", error_message="boom")
        transport, _ = sequenced_transport(
            [
                json_response(200, {"task_uuid": "t1", "status": "pending"}),
                json_response(200, failed),
            ]
        )
        clock = FakeAsyncClock()
        client = AsyncNamiFusion(
            api_key=API_KEY, base_url=BASE_URL, _transport=transport, _sleep=clock.sleep, _now=clock.now
        )

        with pytest.raises(TaskFailedError) as exc_info:
            await client.subscribe("acme/model-x", input={})

        assert exc_info.value.task.status == "failed"
        assert "boom" in exc_info.value.message

    async def test_raises_namifusion_error_with_structured_detail_on_timeout(self):
        processing = make_task(status="processing")
        transport, calls = sequenced_transport(
            [json_response(200, {"task_uuid": "t1", "status": "pending"})]
            + [json_response(200, processing) for _ in range(10)]
        )
        clock = FakeAsyncClock()
        client = AsyncNamiFusion(
            api_key=API_KEY, base_url=BASE_URL, _transport=transport, _sleep=clock.sleep, _now=clock.now
        )

        with pytest.raises(NamiFusionError) as exc_info:
            await client.subscribe("acme/model-x", input={}, poll_interval=2.0, timeout=5.0)

        err = exc_info.value
        assert not isinstance(err, TaskFailedError)
        assert err.detail == {"task_uuid": "t1", "timeout": 5.0}
        assert len(calls) == 3

    async def test_converges_each_request_timeout_to_remaining_budget(self):
        # Async mirror of the sync hard-deadline test: each internal request
        # runs with a budget-converged timeout (5.0 -> 3.0 -> 0.001), never
        # the flat 60s client timeout.
        processing = make_task(status="processing")
        transport, calls = sequenced_transport(
            [json_response(200, {"task_uuid": "t1", "status": "pending"})]
            + [json_response(200, processing) for _ in range(5)]
        )
        clock = FakeAsyncClock()
        client = AsyncNamiFusion(
            api_key=API_KEY,
            base_url=BASE_URL,
            timeout=60.0,
            _transport=transport,
            _sleep=clock.sleep,
            _now=clock.now,
        )

        with pytest.raises(NamiFusionError):
            await client.subscribe("acme/model-x", input={}, poll_interval=2.0, timeout=5.0)

        def read_timeout(req):
            return req.extensions["timeout"]["read"]

        assert len(calls) == 3
        assert read_timeout(calls[0]) == pytest.approx(5.0)
        assert read_timeout(calls[1]) == pytest.approx(3.0)
        assert read_timeout(calls[2]) == pytest.approx(0.001)

    async def test_cancellation_propagates_without_being_swallowed(self):
        # Regression guard for the async-only semantic the brief calls
        # out: an external asyncio.Task.cancel() firing while subscribe()
        # is suspended inside `await sleep(...)` must surface as
        # asyncio.CancelledError to the caller, not be caught/converted
        # by anything in the polling loop.
        processing = make_task(status="processing")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return json_response(200, {"task_uuid": "t1", "status": "pending"})
            return json_response(200, processing)

        transport = httpx.MockTransport(handler)

        real_sleep_calls = []

        async def slow_sleep(seconds: float) -> None:
            real_sleep_calls.append(seconds)
            # A real (tiny) await so the event loop actually suspends
            # here and an external cancel() can land mid-await.
            await asyncio.sleep(0.05)

        client = AsyncNamiFusion(
            api_key=API_KEY,
            base_url=BASE_URL,
            _transport=transport,
            _sleep=slow_sleep,
            _now=lambda: 0.0,
        )

        task = asyncio.ensure_future(client.subscribe("acme/model-x", input={}))
        await asyncio.sleep(0.01)  # let it submit run() and enter the poll sleep
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task
