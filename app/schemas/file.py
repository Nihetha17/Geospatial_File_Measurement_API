"""Pydantic models for upload and file-information responses."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class FileStatus(str, Enum):
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"


class UploadResponse(BaseModel):
    id: str
    filename: str
    feature_count: int
    crs: Optional[str] = Field(default=None, description="Source CRS, or null if the file declares none.")
    status: FileStatus
    warnings: list[str] = Field(default_factory=list)


class FileInfoResponse(BaseModel):
    id: str
    filename: str
    file_type: str = Field(description="'kml' or 'shapefile'.")
    feature_count: int
    crs: Optional[str] = None
    crs_name: Optional[str] = None
    crs_type: Optional[str] = Field(default=None, description="'geographic', 'projected' or 'other'.")
    status: FileStatus
    warnings: list[str] = Field(default_factory=list)
    size_bytes: int
    uploaded_at: datetime
