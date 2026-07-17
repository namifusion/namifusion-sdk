"""Confirms namifusion/__init__.py's public surface: errors, data types,
and (as of Task 5) the NamiFusion / AsyncNamiFusion clients + to_data_url.
"""

import namifusion


def test_exports_all_error_classes():
    for name in (
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
    ):
        assert hasattr(namifusion, name), f"missing export: {name}"


def test_exports_data_types():
    for name in ("Task", "RunResult", "ListTasksResult", "TaskStatus"):
        assert hasattr(namifusion, name), f"missing export: {name}"


def test_version():
    assert namifusion.__version__ == "0.1.0"


def test_exports_client_classes():
    assert hasattr(namifusion, "NamiFusion")
    assert hasattr(namifusion, "AsyncNamiFusion")


def test_exports_to_data_url():
    assert hasattr(namifusion, "to_data_url")
    assert namifusion.to_data_url(b"Hello", "text/plain") == "data:text/plain;base64,SGVsbG8="
