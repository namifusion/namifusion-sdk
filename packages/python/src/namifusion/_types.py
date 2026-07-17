"""Public NamiFusion SDK data types.

Mirrors the shared cross-repo contract
(docs/superpowers/plans/2026-07-17-sdk-contract.md, "SDK 公共 API 契约"
section) and packages/typescript/src/types.ts. These are plain
``dataclasses`` (attribute access — ``task.output`` / ``task.status`` —
not ``TypedDict``/``dict`` subscript access), per the 2026-07-17 contract
decision: fe_web's model-detail page and the agents.md Python examples are
already written against attribute access, so a ``TypedDict`` here would
break every already-shipped example.

Only response data types live here (``_types`` is meant to stay "pure
data"). Client-method option bags (``run()``/``subscribe()`` kwargs, list
filters, etc.) are Task 5's concern — plain keyword arguments on the
client, not dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Dict, List, Mapping, Optional

#: Public task lifecycle. Internal ``polling``/``waiting_callback`` states
#: are mapped to ``processing`` server-side before reaching the client.
#: Kept as a plain ``str`` alias (not ``Literal``) since dataclasses don't
#: enforce ``Literal`` at runtime and the server may pass through other
#: internal status strings in edge cases (mirrors the TS
#: ``ListTasksParams.status`` comment on this).
TaskStatus = str


def _from_dict(cls, data: Mapping[str, Any]):
    """Builds a dataclass instance from a mapping, silently dropping any
    keys that aren't declared fields on ``cls``.

    This is the "服务端多余字段容错" tolerance the contract asks for: the
    API is free to add response fields over time without breaking older
    SDK versions still in the field.
    """
    known = {f.name for f in fields(cls)}
    kwargs = {key: value for key, value in data.items() if key in known}
    return cls(**kwargs)


@dataclass
class Task:
    """``TaskStatusResponse``, as returned by ``GET /run/tasks/{task_uuid}``
    and by webhook callbacks.
    """

    task_uuid: str
    model_id: str
    status: TaskStatus
    created_at: str
    progress: Optional[float] = None
    #: Shape depends on the model's output_schema. Output URLs are
    #: unsigned COS CDN links with an ~7 day lifetime.
    output: Optional[Dict[str, Any]] = None
    cost_credits: Optional[float] = None
    meta_info: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    completed_at: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Task":
        return _from_dict(cls, data)


@dataclass
class RunResult:
    """Response of ``POST /run/{model_id}`` — run is always async."""

    task_uuid: str
    status: TaskStatus
    estimated_time: Optional[float] = None
    output: Optional[Dict[str, Any]] = None
    cost_credits: Optional[float] = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RunResult":
        return _from_dict(cls, data)


@dataclass
class ListTasksResult:
    """Response of ``GET /run/tasks``."""

    total: int
    items: List[Task] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ListTasksResult":
        items = [Task.from_dict(item) for item in data.get("items", [])]
        return cls(total=data.get("total", 0), items=items)
