"""NamiFusion Python SDK.

Task 4 scope: error types and data types only. The client
(``NamiFusion`` / ``AsyncNamiFusion``) lands in Task 5.
"""

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
from ._types import ListTasksResult, RunResult, Task, TaskStatus

__version__ = "0.1.0"

__all__ = [
    "__version__",
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
