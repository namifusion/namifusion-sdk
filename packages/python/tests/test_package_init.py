"""Confirms Task 4's public surface: __init__.py exports errors + types
only (no client — that's Task 5), matching the brief's "暂只导出异常与
类型" instruction.
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


def test_client_not_yet_exported():
    # NamiFusion / AsyncNamiFusion land in Task 5 — Task 4 must not
    # pre-emptively expose (or implement) the client.
    assert not hasattr(namifusion, "NamiFusion")
    assert not hasattr(namifusion, "AsyncNamiFusion")
