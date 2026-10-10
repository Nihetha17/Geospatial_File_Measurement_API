"""Pydantic models for per-feature measurement results."""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class MeasurementStatus(str, Enum):
    MEASURED = "MEASURED"  # area/length calculated (see warnings for caveats)
    NOT_REQUIRED = "NOT_REQUIRED"  # points: no measurement is defined
    NULL_GEOMETRY = "NULL_GEOMETRY"  # feature has no geometry
    EMPTY_GEOMETRY = "EMPTY_GEOMETRY"  # geometry present but empty
    UNSUPPORTED_GEOMETRY = "UNSUPPORTED_GEOMETRY"  # e.g. GeometryCollection
    INVALID_GEOMETRY = "INVALID_GEOMETRY"  # invalid and could not be repaired
    CRS_MISSING = "CRS_MISSING"  # no CRS declared; refused to guess
    MEASUREMENT_FAILED = "MEASUREMENT_FAILED"  # transformation/calculation failed


class FeatureMeasurement(BaseModel):
    feature_index: int = Field(description="0-based position of the feature in the processed file.")
    feature_id: str = Field(description="Feature ID from the source dataset (FID); falls back to the index.")
    layer: Optional[str] = Field(default=None, description="Source layer name (KML folders); null for Shapefiles.")
    geometry_type: Optional[str] = Field(default=None, description="e.g. Polygon, MultiLineString, Point.")
    geometry: Optional[dict[str, Any]] = Field(
        default=None,
        description="GeoJSON geometry object in the SOURCE CRS (coordinates are not reprojected).",
    )
    properties: dict[str, Any] = Field(default_factory=dict)
    measurement_status: MeasurementStatus
    measurement_crs: Optional[str] = Field(
        default=None,
        description="CRS used for the calculation, e.g. 'EPSG:32644', or 'GEODESIC:<ellipsoid>'.",
    )
    area: Optional[float] = None
    area_unit: Optional[str] = None
    length: Optional[float] = None
    length_unit: Optional[str] = None
    message: Optional[str] = Field(default=None, description="Explanation when not measured, or a note.")
    warnings: list[str] = Field(default_factory=list)


class MeasurementsResponse(BaseModel):
    file_id: str
    filename: str
    source_crs: Optional[str] = None
    measurement_crs: Optional[str] = Field(
        default=None,
        description="Measurement CRS if identical for all measured features, 'MULTIPLE' if it varies, else null.",
    )
    feature_count: int
    offset: int = 0
    returned_count: int
    summary: dict[str, int] = Field(description="Feature counts per measurement status.")
    features: list[FeatureMeasurement]
