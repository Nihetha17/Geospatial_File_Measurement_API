"""Application settings.

Settings are a plain frozen dataclass so they are easy to build in tests
(``Settings(database_path=tmp_path / "test.db")``) without touching global
state.  ``Settings.from_env()`` reads ``GEO_*`` environment variables and an
optional ``.env`` file in the project root.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MB = 1024 * 1024


def _load_dotenv(path: Path) -> dict[str, str]:
    """Tiny ``KEY=VALUE`` parser (avoids an extra dependency)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _read_int(env: Mapping[str, str], key: str, default: int, minimum: int = 1) -> int:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"Environment variable {key} must be an integer, got {raw!r}") from None
    if value < minimum:
        raise ValueError(f"Environment variable {key} must be >= {minimum}, got {value}")
    return value


@dataclass(frozen=True)
class Settings:
    app_name: str = "Geospatial File Measurement API"
    # SQLite database file (created automatically, including parent folder).
    database_path: Path = PROJECT_ROOT / "data" / "geospatial.db"
    # Upload limits.
    max_upload_bytes: int = 20 * _MB
    max_features: int = 10_000
    # ZIP archive limits (protection against zip bombs / archive abuse).
    max_zip_entries: int = 200
    max_zip_uncompressed_bytes: int = 200 * _MB
    max_zip_compression_ratio: int = 200
    # Geographic features wider than this (degrees of longitude) are not
    # measured in a single UTM zone; a geodesic calculation is used instead.
    utm_max_lon_span_degrees: float = 6.0

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        if env is None:
            merged = _load_dotenv(PROJECT_ROOT / ".env")
            merged.update(os.environ)
            env = merged
        defaults = cls()
        db_raw = env.get("GEO_DATABASE_PATH")
        db_path = Path(db_raw) if db_raw else defaults.database_path
        if not db_path.is_absolute():
            db_path = PROJECT_ROOT / db_path
        return cls(
            database_path=db_path,
            max_upload_bytes=_read_int(env, "GEO_MAX_UPLOAD_MB", defaults.max_upload_bytes // _MB) * _MB,
            max_features=_read_int(env, "GEO_MAX_FEATURES", defaults.max_features),
            max_zip_entries=_read_int(env, "GEO_MAX_ZIP_ENTRIES", defaults.max_zip_entries),
            max_zip_uncompressed_bytes=_read_int(
                env, "GEO_MAX_ZIP_UNCOMPRESSED_MB", defaults.max_zip_uncompressed_bytes // _MB
            )
            * _MB,
            max_zip_compression_ratio=_read_int(
                env, "GEO_MAX_ZIP_COMPRESSION_RATIO", defaults.max_zip_compression_ratio
            ),
        )
