"""Tests for namifusion._transport — mirrors packages/typescript/tests/http.test.ts's
case set (T2), covering both the sync request() (httpx.Client) and async
arequest() (httpx.AsyncClient) code paths against httpx.MockTransport.

Core scenarios are written as a single `async def test_...(is_async)`
function parametrized over sync/async so retry/error-mapping semantics are
asserted identically for both paths without duplicating each case twice.
pytest-asyncio's `asyncio_mode = "auto"` (see pyproject.toml) means every
`async def test_*` runs inside an event loop regardless of whether the
sync or async branch executes — calling the sync, blocking `request()`
from inside an async test body is safe here because the injected `sleep`
never actually sleeps and MockTransport never touches the network.
"""

import httpx
import pytest

from namifusion import _transport
from namifusion._errors import (
    AuthenticationError,
    InsufficientCreditsError,
    InvalidRequestError,
    RateLimitError,
    ServerError,
)

USER_AGENT = "namifusion-python/0.1.0-test"


def base_opts(**overrides) -> _transport.RequestOptions:
    defaults = dict(
        method="GET",
        url="https://test.namifusion.com/api/v1/marketplace/run/tasks/abc",
        api_key="sk-test-key",
        timeout=5.0,
        max_retries=2,
        user_agent=USER_AGENT,
    )
    defaults.update(overrides)
    return _transport.RequestOptions(**defaults)


def json_response(status: int, body, headers=None) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers or {})


async def run_request(is_async, opts, transport, sleep_calls):
    """Executes opts through either the sync or async transport entry
    point, recording every injected-sleep delay into sleep_calls (a plain
    list) instead of actually sleeping.
    """
    if is_async:

        async def asleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        return await _transport.arequest(opts, transport=transport, sleep=asleep)
    else:

        def sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        return _transport.request(opts, transport=transport, sleep=sleep)


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
class TestCoreRetrySemantics:
    async def test_sends_authorization_and_user_agent_and_content_type(self, is_async):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return json_response(200, {"ok": True})

        transport = httpx.MockTransport(handler)
        result = await run_request(
            is_async,
            base_opts(method="POST", body={"input": {"foo": "bar"}}),
            transport,
            [],
        )

        assert result == {"ok": True}
        assert len(calls) == 1
        req = calls[0]
        assert req.headers["authorization"] == "Bearer sk-test-key"
        assert req.headers["user-agent"] == USER_AGENT
        assert req.headers["content-type"] == "application/json"
        assert req.content == b'{"input": {"foo": "bar"}}'

    async def test_merges_custom_headers(self, is_async):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return json_response(200, {})

        transport = httpx.MockTransport(handler)
        await run_request(
            is_async,
            base_opts(headers={"Idempotency-Key": "abc-123"}),
            transport,
            [],
        )

        assert calls[0].headers["idempotency-key"] == "abc-123"

    async def test_401_maps_to_authentication_error_without_retry(self, is_async):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return json_response(401, {"detail": "Invalid API key"})

        transport = httpx.MockTransport(handler)
        with pytest.raises(AuthenticationError):
            await run_request(is_async, base_opts(), transport, [])
        assert len(calls) == 1

    async def test_402_structured_detail_parsed(self, is_async):
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(
                402,
                {"detail": {"code": "insufficient_credits", "message": "Not enough credits"}},
            )

        transport = httpx.MockTransport(handler)
        with pytest.raises(InsufficientCreditsError) as exc_info:
            await run_request(is_async, base_opts(), transport, [])
        err = exc_info.value
        assert err.code == "insufficient_credits"
        assert str(err) == "Not enough credits"

    async def test_does_not_retry_a_400(self, is_async):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return json_response(400, {"detail": "Bad request"})

        transport = httpx.MockTransport(handler)
        with pytest.raises(InvalidRequestError):
            await run_request(is_async, base_opts(max_retries=2), transport, [])
        assert len(calls) == 1

    async def test_429_retries_honoring_retry_after_then_succeeds(self, is_async):
        calls = []
        responses = iter(
            [
                json_response(429, {"detail": "Too many requests"}, {"Retry-After": "2"}),
                json_response(200, {"ok": True}),
            ]
        )

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return next(responses)

        transport = httpx.MockTransport(handler)
        sleep_calls = []
        result = await run_request(is_async, base_opts(), transport, sleep_calls)

        assert result == {"ok": True}
        assert len(calls) == 2
        # Retry-After (2s) drives the wait, not exponential backoff.
        assert sleep_calls == [2]

    async def test_429_exhausts_retries_and_raises_with_retry_after(self, is_async):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return json_response(429, {"detail": "Too many requests"}, {"Retry-After": "3"})

        transport = httpx.MockTransport(handler)
        with pytest.raises(RateLimitError) as exc_info:
            await run_request(is_async, base_opts(max_retries=2), transport, [])

        # 1 initial attempt + 2 retries = 3 calls total.
        assert len(calls) == 3
        assert exc_info.value.retry_after == 3

    async def test_429_retry_after_capped_at_30(self, is_async):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return json_response(429, {"detail": "Too many requests"}, {"Retry-After": "100"})

        transport = httpx.MockTransport(handler)
        sleep_calls = []
        with pytest.raises(RateLimitError) as exc_info:
            await run_request(is_async, base_opts(max_retries=1), transport, sleep_calls)

        assert exc_info.value.retry_after == 30
        assert sleep_calls == [30]

    async def test_5xx_exhausts_retries_and_raises_server_error(self, is_async):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return json_response(503, {"detail": "Service unavailable"})

        transport = httpx.MockTransport(handler)
        with pytest.raises(ServerError):
            await run_request(is_async, base_opts(max_retries=2), transport, [])

        assert len(calls) == 3

    async def test_network_error_retries_then_raises_original_unchanged(self, is_async):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            raise httpx.ConnectError("connection refused", request=request)

        transport = httpx.MockTransport(handler)
        with pytest.raises(httpx.ConnectError):
            await run_request(is_async, base_opts(max_retries=1), transport, [])

        # 1 initial attempt + 1 retry = 2 calls total.
        assert len(calls) == 2

    async def test_network_error_recovers_on_retry(self, is_async):
        calls = []
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            calls.append(request)
            if attempts["n"] == 1:
                raise httpx.ConnectError("connection refused", request=request)
            return json_response(200, {"ok": True})

        transport = httpx.MockTransport(handler)
        result = await run_request(is_async, base_opts(max_retries=2), transport, [])

        assert result == {"ok": True}
        assert len(calls) == 2

    async def test_backoff_delays_recorded_for_repeated_503(self, is_async):
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(503, {"detail": "Service unavailable"})

        transport = httpx.MockTransport(handler)
        sleep_calls = []
        with pytest.raises(ServerError):
            await run_request(is_async, base_opts(max_retries=2), transport, sleep_calls)

        # 2 backoff waits before giving up (one per retry); each within
        # the documented [0.4, 0.6] (attempt 0) / [0.8, 1.2] (attempt 1)
        # jittered bounds around the 0.5s/1.0s bases.
        assert len(sleep_calls) == 2
        assert 0.4 <= sleep_calls[0] <= 0.6
        assert 0.8 <= sleep_calls[1] <= 1.2


class TestPureRetryFunctions:
    @pytest.mark.parametrize("status", [429, 502, 503, 504])
    def test_should_retry_true_for_retryable_statuses(self, status):
        assert _transport._should_retry(status) is True

    @pytest.mark.parametrize("status", [200, 400, 401, 402, 403, 404, 422, 500, 501])
    def test_should_retry_false_for_non_retryable_statuses(self, status):
        assert _transport._should_retry(status) is False

    def test_backoff_delay_grows_exponentially_and_caps_at_8(self):
        no_jitter = lambda: 0.5  # noqa: E731 - jitter factor 1.0
        assert _transport._backoff_delay(0, random_fn=no_jitter) == pytest.approx(0.5)
        assert _transport._backoff_delay(1, random_fn=no_jitter) == pytest.approx(1.0)
        assert _transport._backoff_delay(2, random_fn=no_jitter) == pytest.approx(2.0)
        assert _transport._backoff_delay(4, random_fn=no_jitter) == pytest.approx(8.0)
        assert _transport._backoff_delay(10, random_fn=no_jitter) == pytest.approx(8.0)

    def test_backoff_delay_applies_up_to_20_percent_jitter(self):
        assert _transport._backoff_delay(1, random_fn=lambda: 0) == pytest.approx(0.8)
        assert _transport._backoff_delay(1, random_fn=lambda: 1) == pytest.approx(1.2)

    def test_backoff_delay_uses_retry_after_verbatim_when_given(self):
        # No jitter applied to an explicit Retry-After wait.
        assert _transport._backoff_delay(5, retry_after=3, random_fn=lambda: 1) == 3
