"""Tests for namifusion._types — dataclasses with attribute access
(task.output / task.status), not TypedDict/dict subscript access, per the
2026-07-17 contract decision. Also covers from_dict()'s tolerance of extra
server-side fields.
"""

from namifusion._types import ListTasksResult, RunResult, Task


class TestTask:
    def test_attribute_access(self):
        task = Task(
            task_uuid="t1",
            model_id="acme/model-x",
            status="completed",
            created_at="2026-07-17T00:00:00Z",
            output={"image_url": "https://cdn.example/x.png"},
        )
        assert task.task_uuid == "t1"
        assert task.status == "completed"
        assert task.output == {"image_url": "https://cdn.example/x.png"}

    def test_optional_fields_default_to_none(self):
        task = Task(
            task_uuid="t1",
            model_id="acme/model-x",
            status="pending",
            created_at="2026-07-17T00:00:00Z",
        )
        assert task.progress is None
        assert task.output is None
        assert task.cost_credits is None
        assert task.meta_info is None
        assert task.error_message is None
        assert task.completed_at is None

    def test_from_dict_builds_from_known_fields(self):
        data = {
            "task_uuid": "t1",
            "model_id": "acme/model-x",
            "status": "completed",
            "progress": 100,
            "output": {"image_url": "https://cdn.example/x.png"},
            "cost_credits": 5,
            "meta_info": {"width": 1024},
            "error_message": None,
            "created_at": "2026-07-17T00:00:00Z",
            "completed_at": "2026-07-17T00:05:00Z",
        }
        task = Task.from_dict(data)
        assert task.task_uuid == "t1"
        assert task.status == "completed"
        assert task.output == {"image_url": "https://cdn.example/x.png"}
        assert task.completed_at == "2026-07-17T00:05:00Z"

    def test_from_dict_ignores_unknown_extra_fields(self):
        data = {
            "task_uuid": "t1",
            "model_id": "acme/model-x",
            "status": "pending",
            "created_at": "2026-07-17T00:00:00Z",
            # Fields the server might add in the future — must not raise
            # or otherwise break construction.
            "brand_new_field": "surprise",
            "internal_debug_info": {"nested": True},
        }
        task = Task.from_dict(data)
        assert task.task_uuid == "t1"
        assert not hasattr(task, "brand_new_field")


class TestRunResult:
    def test_attribute_access(self):
        result = RunResult(task_uuid="t1", status="pending", estimated_time=30)
        assert result.task_uuid == "t1"
        assert result.status == "pending"
        assert result.estimated_time == 30
        assert result.output is None
        assert result.cost_credits is None

    def test_from_dict_ignores_extra_fields(self):
        data = {
            "task_uuid": "t1",
            "status": "pending",
            "estimated_time": 30,
            "output": None,
            "cost_credits": 5,
            "future_field": "surprise",
        }
        result = RunResult.from_dict(data)
        assert result.task_uuid == "t1"
        assert result.cost_credits == 5
        assert not hasattr(result, "future_field")


class TestListTasksResult:
    def test_from_dict_builds_nested_tasks(self):
        data = {
            "total": 2,
            "items": [
                {
                    "task_uuid": "t1",
                    "model_id": "acme/model-x",
                    "status": "completed",
                    "created_at": "2026-07-17T00:00:00Z",
                },
                {
                    "task_uuid": "t2",
                    "model_id": "acme/model-x",
                    "status": "pending",
                    "created_at": "2026-07-17T00:01:00Z",
                },
            ],
        }
        result = ListTasksResult.from_dict(data)
        assert result.total == 2
        assert len(result.items) == 2
        assert all(isinstance(item, Task) for item in result.items)
        assert result.items[0].task_uuid == "t1"

    def test_from_dict_defaults_empty_items(self):
        result = ListTasksResult.from_dict({"total": 0})
        assert result.total == 0
        assert result.items == []
