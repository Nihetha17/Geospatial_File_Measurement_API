"""Plain dataclasses passed between the service layers and the repository."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pyproj import CRS
    from shapely.geometry.base import BaseGeometry


@dataclass
class SourceFeature:
    """One feature as read from the uploaded dataset."""

    index: int
    feature_id: str
    layer: Optional[str]
    geometry: Optional["BaseGeometry"]
    properties: dict[str, Any]


@dataclass
class ParsedDataset:
    file_type: str  # "kml" | "shapefile"
    crs: Optional["CRS"]
    features: list[SourceFeature]
    warnings: list[str] = field(default_factory=list)


@dataclass
class FileRecord:
    """Row of the ``files`` table."""

    id: str
    filename: str
    file_type: str
    feature_count: int
    source_crs: Optional[str]
    source_crs_name: Optional[str]
    source_crs_type: Optional[str]
    measurement_crs: Optional[str]
    status: str
    warnings: list[str]
    summary: dict[str, int]
    size_bytes: int
    uploaded_at: str  # ISO-8601 UTC
