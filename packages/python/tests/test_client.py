"""Tests for namifusion._client.NamiFusion (sync) — mirrors
packages/typescript/tests/client.test.ts's case set (constructor,
run/getTask/listTasks request shape, subscribe polling semantics,
idempotency-key generation/reuse).

HTTP is faked via httpx.MockTransport (same technique as
tests/test_transport.py), injected through the private `_transport`
constructor kwarg. Real waiting is avoided via the private `_sleep`
(and, for subscribe's elapsed-time bookkeeping, `_now`) constructor
kwargs — mirroring `_transport.request()`'s own injectable `sleep`.
These leading-underscore kwargs are intentionally not part of the public
contract (`NamiFusion(api_key=None, base_url=None, max_retries=2,
timeout=60.0)`); they exist purely as test seams.
"""

from __future__ import annotations

import re

import httpx
import pytest

from namifusion import AuthenticationError, NamiFusionError, Task, TaskFailedError
from namifusion._client import NamiFusion

BASE_URL = "https://test.namifusion.com/api/v1/marketplace"
API_KEY = "sk-test-key"

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


class FakeClock:
    """A fake monotonic clock + sleep pair for subscribe() tests: sleep()
    advances the clock by exactly the requested duration instead of
    actually waiting, so poll-interval-backoff / timeout tests run
    instantly and assert exact elapsed-time bookkeeping.
    """

    def __init__(self) -> None:
        self.value = 0.0
        self.sleep_calls: list[float] = []

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
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
    """A MockTransport that returns `responses` (a list of httpx.Response)
    in order, one per request, recording every httpx.Request it saw.
    """
    calls: list[httpx.Request] = []
    it = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return next(it)

    return httpx.MockTransport(handler), calls


class TestConstructor:
    def test_accepts_explicit_api_key(self, monkeypatch):
        monkeypatch.delenv("NAMIFUSION_API_KEY", raising=False)
        NamiFusion(api_key="sk-explicit")  # must not raise

    def test_falls_back_to_env_var(self, monkeypatch):
        monkeypatch.setenv("NAMIFUSION_API_KEY", "sk-from-env")
        NamiFusion()  # must not raise

    def test_prefers_explicit_api_key_over_env(self, monkeypatch):
        monkeypatch.setenv("NAMIFUSION_API_KEY", "sk-from-env")
        transport, calls = sequenced_transport([json_response(200, make_task())])
        client = NamiFusion(api_key="sk-explicit", base_url=BASE_URL, _transport=transport)

        client.run("acme/model-x", input={})

        assert calls[0].headers["authorization"] == "Bearer sk-explicit"

    def test_raises_authentication_error_when_both_missing(self, monkeypatch):
        monkeypatch.delenv("NAMIFUSION_API_KEY", raising=False)
        with pytest.raises(AuthenticationError):
            NamiFusion()

    def test_defaults_base_url_to_production(self):
        transport, calls = sequenced_transport([json_response(200, make_task())])
        client = NamiFusion(api_key=API_KEY, _transport=transport)

        client.run("acme/model-x", input={})

        assert calls[0].url == httpx.URL(
            "https://www.namifusion.com/api/v1/marketplace/run/acme/model-x"
        )

    def test_strips_trailing_slashes_from_base_url(self):
        transport, calls = sequenced_transport([json_response(200, make_task())])
        client = NamiFusion(api_key=API_KEY, base_url=BASE_URL + "///", _transport=transport)

        client.run("acme/model-x", input={})

        assert str(calls[0].url) == f"{BASE_URL}/run/acme/model-x"


class TestRun:
    def test_posts_run_model_id_with_model_id_raw_and_body_input(self):
        transport, calls = sequenced_transport(
            [json_response(200, {"task_uuid": "t1", "status": "pending", "cost_credits": 5})]
        )
        client = NamiFusion(api_key=API_KEY, base_url=BASE_URL, _transport=transport)

        result = client.run("google/nano-banana-pro/text-to-image", input={"prompt": "hi"})

        assert result.task_uuid == "t1"
        assert result.status == "pending"
        assert result.cost_credits == 5
        assert len(calls) == 1
        req = calls[0]
        assert str(req.url) == f"{BASE_URL}/run/google/nano-banana-pro/text-to-image"
        assert req.method == "POST"
        import json as _json

        assert _json.loads(req.content) == {"input": {"prompt": "hi"}}

    def test_auto_generates_uuid_idempotency_key_when_omitted(self):
        transport, calls = sequenced_transport([json_response(200, make_task())])
        client = NamiFusion(api_key=API_KEY, base_url=BASE_URL, _transport=transport)

        client.run("acme/model-x", input={})

        key = calls[0].headers["idempotency-key"]
        assert UUID_RE.match(key)

    def test_sends_caller_supplied_idempotency_key_verbatim(self):
        transport, calls = sequenced_transport([json_response(200, make_task())])
        client = NamiFusion(api_key=API_KEY, base_url=BASE_URL, _transport=transport)

        client.run("acme/model-x", input={}, idempotency_key="caller-key-123")

        assert calls[0].headers["idempotency-key"] == "caller-key-123"

    def test_includes_webhook_url_only_when_provided(self):
        transport, calls = sequenced_transport(
            [json_response(200, make_task()), json_response(200, make_task())]
        )
        client = NamiFusion(api_key=API_KEY, base_url=BASE_URL, _transport=transport)

        import json as _json

        client.run("acme/model-x", input={"a": 1}, webhook_url="https://example.com/hook")
        assert _json.loads(calls[0].content) == {
            "input": {"a": 1},
            "webhook_url": "https://example.com/hook",
        }

        client.run("acme/model-x", input={"a": 1})
        assert _json.loads(calls[1].content) == {"input": {"a": 1}}

    def test_reuses_same_idempotency_key_across_internal_retry(self):
        transport, calls = sequenced_transport(
            [
                json_response(503, {"detail": "Service unavailable"}),
                json_response(200, make_task()),
            ]
        )
        sleep_calls = []
        client = NamiFusion(
            api_key=API_KEY,
            base_url=BASE_URL,
            max_retries=1,
            _transport=transport,
            _sleep=lambda s: sleep_calls.append(s),
        )

        client.run("acme/model-x", input={})

        assert len(calls) == 2
        key1 = calls[0].headers["idempotency-key"]
        key2 = calls[1].headers["idempotency-key"]
        assert key1
        assert key1 == key2

    def test_generates_fresh_idempotency_key_per_independent_call(self):
        # Regression guard: the key must be generated per-call, not cached
        # on the client instance.
        transport, calls = sequenced_transport(
            [json_response(200, make_task()), json_response(200, make_task())]
        )
        client = NamiFusion(api_key=API_KEY, base_url=BASE_URL, _transport=transport)

        client.run("acme/model-x", input={})
        client.run("acme/model-x", input={})

        assert len(calls) == 2
        key1 = calls[0].headers["idempotency-key"]
        key2 = calls[1].headers["idempotency-key"]
        assert key1 and key2
        assert key1 != key2


class TestGetTask:
    def test_gets_run_tasks_task_uuid(self):
        task_body = make_task(status="processing")
        transport, calls = sequenced_transport([json_response(200, task_body)])
        client = NamiFusion(api_key=API_KEY, base_url=BASE_URL, _transport=transport)

        result = client.get_task("t1")

        assert isinstance(result, Task)
        assert result.status == "processing"
        assert str(calls[0].url) == f"{BASE_URL}/run/tasks/t1"
        assert calls[0].method == "GET"


class TestListTasks:
    def test_builds_query_with_skip_limit_model_id_status(self):
        transport, calls = sequenced_transport([json_response(200, {"total": 0, "items": []})])
        client = NamiFusion(api_key=API_KEY, base_url=BASE_URL, _transport=transport)

        client.list_tasks(skip=10, limit=5, model_id="acme/model-x", status="completed")

        assert str(calls[0].url) == (
            f"{BASE_URL}/run/tasks?skip=10&limit=5&model_id=acme%2Fmodel-x&status=completed"
        )
        assert calls[0].method == "GET"

    def test_omits_query_string_when_called_with_no_params(self):
        transport, calls = sequenced_transport([json_response(200, {"total": 0, "items": []})])
        client = NamiFusion(api_key=API_KEY, base_url=BASE_URL, _transport=transport)

        client.list_tasks()

        assert str(calls[0].url) == f"{BASE_URL}/run/tasks"

    def test_converts_items_to_task_dataclasses(self):
        body = {
            "total": 2,
            "items": [make_task(task_uuid="t1"), make_task(task_uuid="t2", status="completed")],
        }
        transport, _ = sequenced_transport([json_response(200, body)])
        client = NamiFusion(api_key=API_KEY, base_url=BASE_URL, _transport=transport)

        result = client.list_tasks()

        assert result.total == 2
        assert all(isinstance(item, Task) for item in result.items)
        assert result.items[0].task_uuid == "t1"
        assert result.items[1].status == "completed"


class TestSubscribe:
    def test_polls_pending_processing_completed_backing_off_and_calls_on_update(self):
        pending = make_task(status="pending")
        processing = make_task(status="processing")
        completed = make_task(status="completed", output={"url": "https://cdn.example/x.png"})

        transport, calls = sequenced_transport(
            [
                json_response(200, {"task_uuid": "t1", "status": "pending"}),
                json_response(200, pending),
                json_response(200, processing),
                json_response(200, completed),
            ]
        )
        clock = FakeClock()
        updates = []
        client = NamiFusion(
            api_key=API_KEY,
            base_url=BASE_URL,
            _transport=transport,
            _sleep=clock.sleep,
            _now=clock.now,
        )

        result = client.subscribe("acme/model-x", input={}, on_update=updates.append)

        assert result.status == "completed"
        assert len(calls) == 4  # 1 run + 3 polls
        assert len(updates) == 3
        assert updates[0].status == "pending"
        assert updates[1].status == "processing"
        assert updates[2].status == "completed"
        # default poll_interval=2.0, x1.5 backoff each poll
        assert clock.sleep_calls == [2.0, 3.0, 4.5]

        poll_req = calls[1]
        assert str(poll_req.url) == f"{BASE_URL}/run/tasks/t1"
        assert poll_req.method == "GET"

    def test_raises_task_failed_error_carrying_terminal_task_on_failed(self):
        failed = make_task(status="failed", error_message="boom")
        transport, _ = sequenced_transport(
            [
                json_response(200, {"task_uuid": "t1", "status": "pending"}),
                json_response(200, failed),
            ]
        )
        clock = FakeClock()
        client = NamiFusion(
            api_key=API_KEY, base_url=BASE_URL, _transport=transport, _sleep=clock.sleep, _now=clock.now
        )

        with pytest.raises(TaskFailedError) as exc_info:
            client.subscribe("acme/model-x", input={})

        err = exc_info.value
        assert err.task.status == "failed"
        assert "boom" in err.message

    def test_raises_task_failed_error_on_cancelled(self):
        cancelled = make_task(status="cancelled")
        transport, _ = sequenced_transport(
            [
                json_response(200, {"task_uuid": "t1", "status": "pending"}),
                json_response(200, cancelled),
            ]
        )
        clock = FakeClock()
        client = NamiFusion(
            api_key=API_KEY, base_url=BASE_URL, _transport=transport, _sleep=clock.sleep, _now=clock.now
        )

        with pytest.raises(TaskFailedError) as exc_info:
            client.subscribe("acme/model-x", input={})

        assert exc_info.value.task.status == "cancelled"

    def test_raises_namifusion_error_with_structured_detail_on_timeout(self):
        processing = make_task(status="processing")
        transport, calls = sequenced_transport(
            [json_response(200, {"task_uuid": "t1", "status": "pending"})]
            + [json_response(200, processing) for _ in range(10)]
        )
        clock = FakeClock()
        updates = []
        client = NamiFusion(
            api_key=API_KEY, base_url=BASE_URL, _transport=transport, _sleep=clock.sleep, _now=clock.now
        )

        with pytest.raises(NamiFusionError) as exc_info:
            client.subscribe(
                "acme/model-x", input={}, poll_interval=2.0, timeout=5.0, on_update=updates.append
            )

        err = exc_info.value
        assert not isinstance(err, TaskFailedError)
        assert "timed out" in err.message.lower()
        assert err.detail == {"task_uuid": "t1", "timeout": 5.0}

        # 1 run POST + 2 polls fit inside the 5.0s budget (waits of 2.0
        # then 3.0 exactly exhaust it; the 3rd wait would start at
        # elapsed=5.0 and is skipped in favor of raising immediately).
        assert len(calls) == 3
        assert len(updates) == 2
        assert clock.sleep_calls == [2.0, 3.0]

    def test_on_update_not_called_again_after_terminal_state_reached(self):
        # Sync has no abort/cancel concept, so the semantic to lock down
        # here is simpler than the TS/async side: once a terminal Task is
        # observed, on_update must have fired exactly once for it and the
        # loop must stop (no further get_task/on_update calls).
        completed = make_task(status="completed")
        transport, calls = sequenced_transport(
            [
                json_response(200, {"task_uuid": "t1", "status": "pending"}),
                json_response(200, completed),
            ]
        )
        clock = FakeClock()
        updates = []
        client = NamiFusion(
            api_key=API_KEY, base_url=BASE_URL, _transport=transport, _sleep=clock.sleep, _now=clock.now
        )

        client.subscribe("acme/model-x", input={}, on_update=updates.append)

        assert len(updates) == 1
        assert updates[0].status == "completed"
        assert len(calls) == 2  # no poll after the terminal one

    def test_defaults_poll_interval_and_timeout(self):
        import inspect

        sig = inspect.signature(NamiFusion.subscribe)
        assert sig.parameters["poll_interval"].default == 2.0
        assert sig.parameters["timeout"].default == 1800.0

    def test_subscribe_passes_through_idempotency_key_and_webhook_url_to_run(self):
        completed = make_task(status="completed")
        transport, calls = sequenced_transport(
            [
                json_response(200, {"task_uuid": "t1", "status": "pending"}),
                json_response(200, completed),
            ]
        )
        clock = FakeClock()
        client = NamiFusion(
            api_key=API_KEY, base_url=BASE_URL, _transport=transport, _sleep=clock.sleep, _now=clock.now
        )

        client.subscribe(
            "acme/model-x",
            input={},
            webhook_url="https://example.com/hook",
            idempotency_key="my-key",
        )

        run_req = calls[0]
        assert run_req.headers["idempotency-key"] == "my-key"
        import json as _json

        assert _json.loads(run_req.content) == {
            "input": {},
            "webhook_url": "https://example.com/hook",
        }
