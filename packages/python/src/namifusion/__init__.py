"""NamiFusion Python SDK.

Public surface: the ``NamiFusion`` (sync) / ``AsyncNamiFusion`` (async)
clients, the ``to_data_url`` file helper, the full error taxonomy, and
the response data types (``Task``/``RunResult``/``ListTasksResult``).
Mirrors packages/typescript/src/index.ts and the cross-repo shared
contract (docs/superpowers/plans/2026-07-17-sdk-contract.md).
"""

from ._client import AsyncNamiFusion, NamiFusion
from ._errors import (
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
)
from ._files import to_data_url
from ._types import ListTasksResult, RunResult, Task, TaskStatus

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "NamiFusion",
    "AsyncNamiFusion",
    "to_data_url",
    "NamiFusionError",
    "AuthenticationError",
    "InsufficientCreditsError",
    "ForbiddenError",
    "NotFoundError",
    "InvalidRequestError",
    "RateLimitError",
    "ServerError",
    "TaskFailedError",
    "error_from_response",
    "Task",
    "RunResult",
    "ListTasksResult",
    "TaskStatus",
]
