"""ZIP-extraction attacks and upload size limits."""
import io
import os
import zipfile
from dataclasses import replace

import pytest

from tests.helpers import kml_document, point_placemark, post_file, zip_bytes

SHP_PARTS = {"data.shp": b"x", "data.shx": b"x", "data.dbf": b"x"}


def _code(response):
    return response.json()["error"]["code"]


@pytest.mark.parametrize(
    "evil_name",
    ["../evil.shp", "..\\evil.shp", "/etc/passwd", "C:/Windows/evil.shp", "ok/../../evil.shp"],
)
def test_zip_path_traversal_is_rejected(client, evil_name):
    response = post_file(client, "evil.zip", zip_bytes({**SHP_PARTS, evil_name: b"payload"}))
    assert response.status_code == 400
    assert _code(response) == "UNSAFE_ARCHIVE"


def test_zip_symlink_entry_is_rejected(client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in SHP_PARTS.items():
            zf.writestr(name, data)
        link = zipfile.ZipInfo("link.shp")
        link.external_attr = (0o120777 << 16)
        zf.writestr(link, "/etc/passwd")
    response = post_file(client, "link.zip", buffer.getvalue())
    assert response.status_code == 400
    assert _code(response) == "UNSAFE_ARCHIVE"


def test_zip_bomb_by_compression_ratio_is_rejected(client):
    bomb = zip_bytes({**SHP_PARTS, "padding.bin": b"\x00" * (5 * 1024 * 1024)})
    assert len(bomb) < 100_000  # tiny upload, huge expansion
    response = post_file(client, "bomb.zip", bomb)
    assert response.status_code == 400
    assert _code(response) == "UNSAFE_ARCHIVE"


def test_zip_total_uncompressed_size_limit(settings, make_client):
    limited = make_client(replace(settings, max_zip_uncompressed_bytes=2000))
    response = post_file(limited, "big.zip", zip_bytes({**SHP_PARTS, "random.bin": os.urandom(5000)}))
    assert response.status_code == 413
    assert _code(response) == "ARCHIVE_TOO_LARGE"


def test_zip_entry_count_limit(settings, make_client):
    limited = make_client(replace(settings, max_zip_entries=5))
    files = {f"file{i}.txt": b"x" for i in range(10)}
    response = post_file(limited, "many.zip", zip_bytes(files))
    assert response.status_code == 400
    assert _code(response) == "UNSAFE_ARCHIVE"


def test_encrypted_flag_is_rejected(client):
    blob = bytearray(zip_bytes(SHP_PARTS))
    # Set the "encrypted" general-purpose flag (bit 0) in every local and central header.
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        start = 0
        while (position := blob.find(signature, start)) != -1:
            blob[position + flag_offset] |= 0x01
            start = position + 4
    response = post_file(client, "encrypted.zip", bytes(blob))
    assert response.status_code == 400
    assert _code(response) in {"UNSAFE_ARCHIVE", "INVALID_ZIP"}


def test_upload_size_limit_returns_413(settings, make_client):
    limited = make_client(replace(settings, max_upload_bytes=1000))
    content = kml_document(*[point_placemark(f"p{i}", 80.2, 13.0) for i in range(200)])
    assert len(content) > 1000
    response = post_file(limited, "big.kml", content)
    assert response.status_code == 413
    assert _code(response) == "FILE_TOO_LARGE"


def test_error_responses_do_not_leak_paths_or_tracebacks(client):
    response = post_file(client, "broken.kml", b"<kml><Document>")
    text = response.text
    assert "Traceback" not in text
    assert "/tmp" not in text and "geoapi_" not in text and "site-packages" not in text


def test_uploaded_filename_never_touches_disk(client, tmp_path):
    """The name is only stored as text; hostile names are neutralised."""
    content = kml_document(point_placemark("pin", 80.2, 13.0))
    response = post_file(client, "..\\..\\windows\\system32\\x.kml", content)
    assert response.status_code == 201
    assert response.json()["filename"] == "x.kml"
