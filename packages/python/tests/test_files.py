"""Tests for namifusion._files.to_data_url — mirrors
packages/typescript/tests/client.test.ts's `toDataUrl` cases (base64
encoding correctness), minus the multi-input-type overloads: the Python
contract only accepts ``bytes`` (no Blob/ArrayBuffer equivalent).
"""

from namifusion._files import to_data_url


def test_encodes_bytes_as_base64_data_url():
    assert to_data_url(b"Hello", "text/plain") == "data:text/plain;base64,SGVsbG8="


def test_encodes_empty_bytes():
    assert to_data_url(b"", "application/octet-stream") == "data:application/octet-stream;base64,"


def test_encodes_binary_png_like_data():
    data = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
    url = to_data_url(data, "image/png")
    assert url.startswith("data:image/png;base64,")
    import base64

    b64 = url.split(",", 1)[1]
    assert base64.b64decode(b64) == data
