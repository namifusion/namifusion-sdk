"""NamiFusion SDK exception types.

Mirrors packages/typescript/src/errors.ts — same taxonomy and the same
``error_from_response()`` parsing semantics for the FastAPI
``{"detail": ...}`` envelope, so behavior stays aligned across both SDKs.
"""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING, Any, Mapping, Optional, Tuple

if TYPE_CHECKING:
    from ._types import Task


class NamiFusionError(Exception):
    """Base class for all errors raised by the NamiFusion SDK.

    ``status`` is the HTTP status code when this error originated from an
    HTTP response, or 0 for errors not tied to a single HTTP response (see
    ``TaskFailedError``).
    """

    def __init__(
        self,
        message: str,
        status: int = 0,
        *,
        code: Optional[str] = None,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.detail = detail

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(message={self.message!r}, "
            f"status={self.status!r}, code={self.code!r})"
        )


class AuthenticationError(NamiFusionError):
    """401 — invalid or missing credentials."""

    def __init__(
        self,
        message: str = "Authentication failed",
        *,
        code: Optional[str] = None,
        detail: Any = None,
    ) -> None:
        super().__init__(message, 401, code=code, detail=detail)


class InsufficientCreditsError(NamiFusionError):
    """402 — account lacks sufficient credits to run this request."""

    def __init__(
        self,
        message: str = "Insufficient credits",
        *,
        code: Optional[str] = None,
        detail: Any = None,
    ) -> None:
        super().__init__(message, 402, code=code, detail=detail)


class ForbiddenError(NamiFusionError):
    """403 — authenticated but not authorized for this resource."""

    def __init__(
        self,
        message: str = "Forbidden",
        *,
        code: Optional[str] = None,
        detail: Any = None,
    ) -> None:
        super().__init__(message, 403, code=code, detail=detail)


class NotFoundError(NamiFusionError):
    """404 — resource (e.g. task_uuid) not found."""

    def __init__(
        self,
        message: str = "Not found",
        *,
        code: Optional[str] = None,
        detail: Any = None,
    ) -> None:
        super().__init__(message, 404, code=code, detail=detail)


class InvalidRequestError(NamiFusionError):
    """400 or 422 — malformed or invalid request."""

    def __init__(
        self,
        message: str = "Invalid request",
        status: int = 400,
        *,
        code: Optional[str] = None,
        detail: Any = None,
    ) -> None:
        super().__init__(message, status, code=code, detail=detail)


class RateLimitError(NamiFusionError):
    """429 — rate limited (per-second throttling) or monthly quota
    exceeded. ``retry_after`` (seconds) is only present for the throttling
    case.
    """

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        *,
        code: Optional[str] = None,
        detail: Any = None,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message, 429, code=code, detail=detail)
        self.retry_after = retry_after


class ServerError(NamiFusionError):
    """5xx — server-side failure."""

    def __init__(
        self,
        message: str = "Server error",
        status: int = 500,
        *,
        code: Optional[str] = None,
        detail: Any = None,
    ) -> None:
        super().__init__(message, status, code=code, detail=detail)


class TaskFailedError(NamiFusionError):
    """Raised by ``client.subscribe()`` when the task reaches a terminal
    ``failed``/``cancelled`` state. Not an HTTP-response error, so
    ``status`` is 0; the terminal ``Task`` is attached for inspection.
    """

    def __init__(
        self,
        message: str,
        task: "Task",
        *,
        code: Optional[str] = None,
        detail: Any = None,
    ) -> None:
        super().__init__(message, 0, code=code, detail=detail)
        self.task = task


def _parse_error_body(body: Any) -> Tuple[str, Optional[str], Any]:
    """Parses the FastAPI ``{"detail": ...}`` error envelope. ``detail``
    is either a plain string, or (seen on 402) a structured
    ``{code, message}`` object. Returns ``(message, code, detail)``.
    """
    if isinstance(body, Mapping) and "detail" in body:
        detail = body["detail"]

        if isinstance(detail, str):
            return detail, None, detail

        if isinstance(detail, Mapping):
            code = detail.get("code") if isinstance(detail.get("code"), str) else None
            message = detail.get("message")
            if not isinstance(message, str):
                message = json.dumps(detail)
            return message, code, detail

        if detail is not None:
            return str(detail), None, detail

    return "Request failed", None, body


def parse_retry_after_seconds(headers: Mapping[str, str]) -> Optional[float]:
    """Parses the ``Retry-After`` header as a number of seconds, capped at
    30s. Returns ``None`` when absent or unparseable (e.g. the
    monthly-quota flavor of 429, which carries no Retry-After). Shared by
    ``_transport``'s retry loop so both call sites use the same
    parsing/cap logic.
    """
    raw = headers.get("retry-after")
    if not raw:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return min(seconds, 30)


def error_from_response(status: int, body: Any, headers: Mapping[str, str]) -> NamiFusionError:
    """Maps an HTTP error response to the corresponding ``NamiFusionError``
    subclass, parsing the FastAPI ``{"detail": ...}`` body along the way.
    """
    message, code, detail = _parse_error_body(body)

    if status == 401:
        return AuthenticationError(message, code=code, detail=detail)
    if status == 402:
        return InsufficientCreditsError(message, code=code, detail=detail)
    if status == 403:
        return ForbiddenError(message, code=code, detail=detail)
    if status == 404:
        return NotFoundError(message, code=code, detail=detail)
    if status == 429:
        return RateLimitError(
            message,
            code=code,
            detail=detail,
            retry_after=parse_retry_after_seconds(headers),
        )
    if status in (400, 422):
        return InvalidRequestError(message, status, code=code, detail=detail)
    if status >= 500:
        return ServerError(message, status, code=code, detail=detail)

    # Fallback for any other 4xx/unexpected status: still a real
    # NamiFusionError, just without a more specific subclass.
    return NamiFusionError(message, status, code=code, detail=detail)
