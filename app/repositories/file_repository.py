"""SQLite persistence for file metadata and processed measurement results.

Design notes
------------
* Uses the standard-library ``sqlite3`` module; no ORM is needed for two tables.
* A new connection is opened per operation (cheap for SQLite) which makes the
  repository safe to use from FastAPI's worker threads and ensures the
  database file is released immediately (important on Windows).
* Each processed feature is stored as one JSON document (the exact structure
  returned by the API), so reads need no re-processing and no geometry library.
* All SQL uses bound parameters (no string formatting -> no SQL injection).
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Optional

from app.models.domain import FileRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id               TEXT PRIMARY KEY,
    filename         TEXT NOT NULL,
    file_type        TEXT NOT NULL,
    feature_count    INTEGER NOT NULL,
    source_crs       TEXT,
    source_crs_name  TEXT,
    source_crs_type  TEXT,
    measurement_crs  TEXT,
    status           TEXT NOT NULL,
    warnings_json    TEXT NOT NULL,
    summary_json     TEXT NOT NULL,
    size_bytes       INTEGER NOT NULL,
    uploaded_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS features (
    file_id        TEXT NOT NULL,
    feature_index  INTEGER NOT NULL,
    data_json      TEXT NOT NULL,
    PRIMARY KEY (file_id, feature_index),
    FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE
);
"""


class FileRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.database_path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_db(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------ writes
    def save(self, record: FileRecord, features: list[dict[str, Any]]) -> None:
        """Insert the file row and all feature rows in a single transaction."""
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """INSERT INTO files (id, filename, file_type, feature_count, source_crs,
                       source_crs_name, source_crs_type, measurement_crs, status,
                       warnings_json, summary_json, size_bytes, uploaded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.id,
                    record.filename,
                    record.file_type,
                    record.feature_count,
                    record.source_crs,
                    record.source_crs_name,
                    record.source_crs_type,
                    record.measurement_crs,
                    record.status,
                    json.dumps(record.warnings),
                    json.dumps(record.summary),
                    record.size_bytes,
                    record.uploaded_at,
                ),
            )
            conn.executemany(
                "INSERT INTO features (file_id, feature_index, data_json) VALUES (?, ?, ?)",
                [
                    (record.id, feature["feature_index"], json.dumps(feature, allow_nan=False))
                    for feature in features
                ],
            )

    # ------------------------------------------------------------------- reads
    def get_file(self, file_id: str) -> Optional[FileRecord]:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        if row is None:
            return None
        return FileRecord(
            id=row["id"],
            filename=row["filename"],
            file_type=row["file_type"],
            feature_count=row["feature_count"],
            source_crs=row["source_crs"],
            source_crs_name=row["source_crs_name"],
            source_crs_type=row["source_crs_type"],
            measurement_crs=row["measurement_crs"],
            status=row["status"],
            warnings=json.loads(row["warnings_json"]),
            summary=json.loads(row["summary_json"]),
            size_bytes=row["size_bytes"],
            uploaded_at=row["uploaded_at"],
        )

    def get_features(
        self, file_id: str, limit: Optional[int] = None, offset: int = 0
    ) -> list[dict[str, Any]]:
        sql = "SELECT data_json FROM features WHERE file_id = ? ORDER BY feature_index"
        params: list[Any] = [file_id]
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params += [limit, offset]
        elif offset:
            sql += " LIMIT -1 OFFSET ?"
            params.append(offset)
        with closing(self._connect()) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [json.loads(row["data_json"]) for row in rows]
