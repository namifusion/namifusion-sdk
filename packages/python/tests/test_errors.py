"""Tests for namifusion._errors — mirrors packages/typescript/tests/http.test.ts's
error-mapping cases (T2 test set) so behavior stays aligned across SDKs.
"""

import httpx
import pytest

from namifusion._errors import (
    AuthenticationError,
    ForbiddenError,
    InsufficientCreditsError,
    InvalidRequestError,
    NamiFusionError,
    NotFoundError,
    RateLimitError,
    ServerError,
    TaskFailedError,
    error_from_response,
    parse_retry_after_seconds,
)
from namifusion._types import Task


def make_task(**overrides):
    defaults = dict(
        task_uuid="t1",
        model_id="acme/model-x",
        status="failed",
        created_at="2026-07-17T00:00:00Z",
    )
    defaults.update(overrides)
    return Task(**defaults)


class TestNamiFusionErrorBase:
    def test_base_attributes(self):
        err = NamiFusionError("boom", 418, code="teapot", detail={"a": 1})
        assert str(err) == "boom"
        assert err.status == 418
        assert err.code == "teapot"
        assert err.detail == {"a": 1}

    def test_defaults_when_no_code_or_detail(self):
        err = NamiFusionError("boom", 500)
        assert err.code is None
        assert err.detail is None

    def test_all_subclasses_are_namifusion_error(self):
        for cls in (
            AuthenticationError,
            InsufficientCreditsError,
            ForbiddenError,
            NotFoundError,
            InvalidRequestError,
            RateLimitError,
            ServerError,
        ):
            assert issubclass(cls, NamiFusionError)


class TestTaskFailedError:
    def test_carries_task_and_status_zero(self):
        task = make_task(status="failed", error_message="model exploded")
        err = TaskFailedError("Task failed: model exploded", task)
        assert err.status == 0
        assert err.task is task
        assert err.task.status == "failed"
        assert isinstance(err, NamiFusionError)


class TestErrorFromResponse:
    def test_401_maps_to_authentication_error(self):
        err = error_from_response(401, {"detail": "Invalid API key"}, httpx.Headers())
        assert isinstance(err, AuthenticationError)
        assert err.status == 401
        assert str(err) == "Invalid API key"
        assert err.detail == "Invalid API key"
        assert err.code is None

    def test_402_string_detail(self):
        err = error_from_response(402, {"detail": "Insufficient credits"}, httpx.Headers())
        assert isinstance(err, InsufficientCreditsError)
        assert err.status == 402
        assert str(err) == "Insufficient credits"
        assert err.detail == "Insufficient credits"
        assert err.code is None

    def test_402_structured_detail(self):
        body = {"detail": {"code": "insufficient_credits", "message": "Not enough credits"}}
        err = error_from_response(402, body, httpx.Headers())
        assert isinstance(err, InsufficientCreditsError)
        assert err.status == 402
        assert err.code == "insufficient_credits"
        assert str(err) == "Not enough credits"
        assert err.detail == {"code": "insufficient_credits", "message": "Not enough credits"}

    def test_403_maps_to_forbidden(self):
        err = error_from_response(403, {"detail": "nope"}, httpx.Headers())
        assert isinstance(err, ForbiddenError)
        assert err.status == 403

    def test_404_maps_to_not_found(self):
        err = error_from_response(404, {"detail": "no such task"}, httpx.Headers())
        assert isinstance(err, NotFoundError)
        assert err.status == 404

    @pytest.mark.parametrize("status", [400, 422])
    def test_400_and_422_map_to_invalid_request(self, status):
        err = error_from_response(status, {"detail": "bad input"}, httpx.Headers())
        assert isinstance(err, InvalidRequestError)
        assert err.status == status

    def test_429_with_retry_after_header(self):
        headers = httpx.Headers({"Retry-After": "3"})
        err = error_from_response(429, {"detail": "Too many requests"}, headers)
        assert isinstance(err, RateLimitError)
        assert err.status == 429
        assert err.retry_after == 3

    def test_429_without_retry_after_header_monthly_quota(self):
        err = error_from_response(429, {"detail": "Monthly quota exceeded"}, httpx.Headers())
        assert isinstance(err, RateLimitError)
        assert err.retry_after is None

    @pytest.mark.parametrize("status", [500, 502, 503, 504])
    def test_5xx_maps_to_server_error(self, status):
        err = error_from_response(status, {"detail": "boom"}, httpx.Headers())
        assert isinstance(err, ServerError)
        assert err.status == status

    def test_unknown_4xx_falls_back_to_base_error(self):
        err = error_from_response(418, {"detail": "I'm a teapot"}, httpx.Headers())
        assert type(err) is NamiFusionError
        assert err.status == 418

    def test_body_without_detail_key_falls_back(self):
        err = error_from_response(500, {"unexpected": "shape"}, httpx.Headers())
        assert isinstance(err, ServerError)
        assert err.detail == {"unexpected": "shape"}

    def test_non_string_non_object_detail_is_stringified(self):
        err = error_from_response(400, {"detail": 42}, httpx.Headers())
        assert isinstance(err, InvalidRequestError)
        assert str(err) == "42"
        assert err.detail == 42


class TestParseRetryAfterSeconds:
    def test_caps_above_30_down_to_30(self):
        assert parse_retry_after_seconds(httpx.Headers({"Retry-After": "100"})) == 30

    def test_passes_through_values_at_or_below_cap(self):
        assert parse_retry_after_seconds(httpx.Headers({"Retry-After": "30"})) == 30
        assert parse_retry_after_seconds(httpx.Headers({"Retry-After": "5"})) == 5

    def test_absent_header_returns_none(self):
        assert parse_retry_after_seconds(httpx.Headers()) is None

    def test_unparseable_header_returns_none(self):
        assert parse_retry_after_seconds(httpx.Headers({"Retry-After": "not-a-number"})) is None

    def test_negative_header_returns_none(self):
        assert parse_retry_after_seconds(httpx.Headers({"Retry-After": "-5"})) is None
