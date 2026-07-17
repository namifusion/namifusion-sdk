"""File helpers for the NamiFusion SDK.

Mirrors packages/typescript/src/files.ts. The API has no public upload
endpoint — file inputs go through a model's `auto_upload_base64`
parameter as a base64 string or data URL — so `to_data_url` is the
primary way callers turn binary data into something they can put in
`input`.
"""

from __future__ import annotations

import base64


def to_data_url(data: bytes, mime_type: str) -> str:
    """Encodes binary data as a ``data:`` URL (``data:{mime_type};base64,...``)."""
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"
