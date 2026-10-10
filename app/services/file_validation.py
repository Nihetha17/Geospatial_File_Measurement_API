"""Upload validation: filenames, size-limited saving, KML content checks and
safe ShapeFile ZIP extraction.

This module deliberately depends only on the standard library and
``app.core`` so its security behaviour is easy to read and to test.

ZIP hardening summary
---------------------
* entry count, declared total size and compression ratio are limited;
* absolute paths, drive letters, ``..`` segments and NUL bytes are rejected;
* symlinks and encrypted entries are rejected;
* only the whitelisted Shapefile components are extracted, and they are written
  to *fixed* names (``dataset.shp`` ...) - archive-supplied names never reach
  the file system;
* extraction is streamed with a hard byte budget, so a lying ZIP header cannot
  produce more data than the configured limit.
"""
from __future__ import annotations

import os
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path
from typing import BinaryIO, Optional

from app.core.config import Settings
from app.core.exceptions import AppError

ALLOWED_EXTENSIONS = (".kml", ".zip")
SHAPEFILE_REQUIRED = (".shp", ".shx", ".dbf")
SHAPEFILE_OPTIONAL = (".prj", ".cpg")
_SHAPEFILE_EXTENSIONS = SHAPEFILE_REQUIRED + SHAPEFILE_OPTIONAL
_CHUNK = 1024 * 1024
_RATIO_CHECK_MIN_BYTES = 1024 * 1024  # tiny files may legitimately compress very well


# --------------------------------------------------------------------- names
def sanitize_filename(raw: Optional[str], max_length: int = 100) -> str:
    """Return a harmless display name. It is *never* used as a file-system path."""
    name = (raw or "").replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable())
    name = re.sub(r"[^\w.\- ()]", "_", name, flags=re.UNICODE)
    name = name.strip(" .")
    if not name:
        return "upload"
    if len(name) > max_length:
        stem, ext = os.path.splitext(name)
        name = stem[: max(1, max_length - len(ext))] + ext
    return name


def get_extension(filename: str) -> str:
    return os.path.splitext(filename)[1].lower()


def check_extension(filename: str) -> str:
    ext = get_extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        shown = ext if ext else "(none)"
        raise AppError(
            415,
            "UNSUPPORTED_FILE_TYPE",
            f"Unsupported file type {shown}. Upload a .kml file or a .zip containing a Shapefile.",
            {"allowed_extensions": list(ALLOWED_EXTENSIONS)},
        )
    return ext


# ------------------------------------------------------------------- saving
def save_upload(stream: BinaryIO, destination: Path, max_bytes: int) -> int:
    """Copy an upload to disk in chunks, aborting as soon as the limit is exceeded."""
    total = 0
    with open(destination, "wb") as out:
        while True:
            chunk = stream.read(_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise AppError(
                    413,
                    "FILE_TOO_LARGE",
                    f"The uploaded file exceeds the maximum allowed size of {max_bytes} bytes.",
                    {"max_bytes": max_bytes},
                )
            out.write(chunk)
    if total == 0:
        raise AppError(400, "EMPTY_FILE", "The uploaded file is empty.")
    return total


# ---------------------------------------------------------------------- KML
_DTD_PATTERN = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


def validate_kml(path: Path) -> None:
    """Check that the file is well-formed KML before handing it to GDAL."""
    data = path.read_bytes()
    if b"\x00" in data:
        raise AppError(
            422, "INVALID_KML", "The file is not a UTF-8/ASCII text KML document (binary content detected)."
        )
    if _DTD_PATTERN.search(data):
        # Blocks entity-expansion ("billion laughs") and external-entity tricks.
        raise AppError(422, "INVALID_KML", "KML files containing DOCTYPE or ENTITY declarations are not accepted.")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise AppError(422, "INVALID_KML", "The file is not well-formed XML, so it cannot be a valid KML document.") from None
    local_name = root.tag.rsplit("}", 1)[-1].lower()
    if local_name != "kml":
        raise AppError(422, "INVALID_KML", "The XML document does not have a <kml> root element.")


# --------------------------------------------------------------------- ZIP
def _assert_safe_name(name: str) -> None:
    normalized = name.replace("\\", "/")
    parts = normalized.split("/")
    if (
        "\x00" in name
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or any(part == ".." for part in parts)
    ):
        raise AppError(
            400,
            "UNSAFE_ARCHIVE",
            "The ZIP archive contains an unsafe path (absolute path or path traversal) and was rejected.",
        )


def _check_archive(infos: list[zipfile.ZipInfo], settings: Settings) -> None:
    if len(infos) > settings.max_zip_entries:
        raise AppError(
            400,
            "UNSAFE_ARCHIVE",
            f"The ZIP archive contains too many entries (limit {settings.max_zip_entries}).",
        )
    declared_total = 0
    for info in infos:
        _assert_safe_name(info.filename)
        if info.flag_bits & 0x1:
            raise AppError(400, "UNSAFE_ARCHIVE", "Encrypted ZIP entries are not supported.")
        if ((info.external_attr >> 16) & 0o170000) == 0o120000:
            raise AppError(400, "UNSAFE_ARCHIVE", "The ZIP archive contains a symbolic link and was rejected.")
        declared_total += info.file_size
        if info.file_size > _RATIO_CHECK_MIN_BYTES:
            ratio = info.file_size / max(info.compress_size, 1)
            if ratio > settings.max_zip_compression_ratio:
                raise AppError(
                    400,
                    "UNSAFE_ARCHIVE",
                    "The ZIP archive has a suspicious compression ratio (possible zip bomb) and was rejected.",
                )
    if declared_total > settings.max_zip_uncompressed_bytes:
        raise AppError(
            413,
            "ARCHIVE_TOO_LARGE",
            f"The ZIP archive would expand beyond the limit of {settings.max_zip_uncompressed_bytes} bytes.",
        )


def _select_shapefile(infos: list[zipfile.ZipInfo]) -> dict[str, zipfile.ZipInfo]:
    """Group entries by (folder, stem) and return the single Shapefile dataset."""
    groups: dict[tuple[str, str], dict[str, zipfile.ZipInfo]] = {}
    for info in infos:
        if info.is_dir():
            continue
        normalized = info.filename.replace("\\", "/")
        base = posixpath.basename(normalized)
        if normalized.startswith("__MACOSX/") or base.startswith("._"):
            continue  # macOS metadata
        stem, ext = posixpath.splitext(base)
        ext = ext.lower()
        if ext not in _SHAPEFILE_EXTENSIONS:
            continue
        key = (posixpath.dirname(normalized).lower(), stem.lower())
        members = groups.setdefault(key, {})
        if ext in members:
            raise AppError(422, "INVALID_SHAPEFILE_ARCHIVE", f"The ZIP archive contains duplicate '{ext}' entries.")
        members[ext] = info

    with_shp = {key: members for key, members in groups.items() if ".shp" in members}
    if len(with_shp) > 1:
        raise AppError(
            422,
            "MULTIPLE_DATASETS",
            "The ZIP archive contains more than one Shapefile. Upload exactly one Shapefile per ZIP.",
            {"dataset_count": len(with_shp)},
        )
    if with_shp:
        members = next(iter(with_shp.values()))
    elif groups:
        members = next(iter(groups.values()))
    else:
        members = {}
    missing = [ext for ext in SHAPEFILE_REQUIRED if ext not in members]
    if missing:
        raise AppError(
            422,
            "MISSING_SHAPEFILE_COMPONENTS",
            "The ZIP archive does not contain a complete Shapefile. Required components: .shp, .shx and .dbf.",
            {"missing_components": missing},
        )
    return members


def _extract_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, destination: Path, budget: int) -> int:
    written = 0
    with zf.open(info) as src, open(destination, "wb") as out:
        while True:
            chunk = src.read(64 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > budget:
                raise AppError(413, "ARCHIVE_TOO_LARGE", "The ZIP archive expands beyond the allowed size limit.")
            out.write(chunk)
    return written


def extract_shapefile_zip(zip_path: Path, dest_dir: Path, settings: Settings) -> Path:
    """Validate a ZIP and extract its Shapefile to ``dest_dir/dataset.*``.

    Returns the path of ``dataset.shp``.
    """
    invalid = AppError(400, "INVALID_ZIP", "The file is not a valid or readable ZIP archive.")
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError):
        raise invalid from None
    with zf:
        infos = zf.infolist()
        _check_archive(infos, settings)
        members = _select_shapefile(infos)
        dest_dir.mkdir(parents=True, exist_ok=True)
        remaining = settings.max_zip_uncompressed_bytes
        try:
            for ext, info in members.items():
                remaining -= _extract_member(zf, info, dest_dir / f"dataset{ext}", remaining)
        except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError):
            raise invalid from None
    return dest_dir / "dataset.shp"
