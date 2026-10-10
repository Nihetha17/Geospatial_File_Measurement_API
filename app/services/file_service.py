"""Orchestrates an upload: validate -> read -> measure -> persist -> respond."""
from __future__ import annotations

import json
import logging
import tempfile
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Optional

import shapely

from app.core.config import Settings
from app.core.exceptions import AppError
from app.models.domain import FileRecord, ParsedDataset, SourceFeature
from app.repositories.file_repository import FileRepository
from app.schemas.file import FileInfoResponse, FileStatus, UploadResponse
from app.schemas.measurement import (
    FeatureMeasurement,
    MeasurementsResponse,
    MeasurementStatus,
)
from app.services import file_validation
from app.services.crs_utils import CrsContext, build_crs_context
from app.services.geospatial_processor import read_kml, read_shapefile
from app.services.measurement_service import (
    AREA_UNIT,
    LENGTH_UNIT,
    MeasurementResult,
    measure_geometry,
)

logger = logging.getLogger("geospatial_api")

_CLEAN_STATUSES = {MeasurementStatus.MEASURED, MeasurementStatus.NOT_REQUIRED}


def _geometry_to_geojson(geometry) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    if geometry is None:
        return None, None
    try:
        return json.loads(shapely.to_geojson(geometry)), None
    except (ValueError, TypeError):
        return None, "The geometry could not be serialised to GeoJSON (non-finite coordinates)."


def _build_feature(source: SourceFeature, result: MeasurementResult) -> FeatureMeasurement:
    geometry_json, geojson_warning = _geometry_to_geojson(source.geometry)
    warnings = list(result.warnings)
    if geojson_warning:
        warnings.append(geojson_warning)
    return FeatureMeasurement(
        feature_index=source.index,
        feature_id=source.feature_id,
        layer=source.layer,
        geometry_type=source.geometry.geom_type if source.geometry is not None else None,
        geometry=geometry_json,
        properties=source.properties,
        measurement_status=result.status,
        measurement_crs=result.measurement_crs,
        area=result.area,
        area_unit=AREA_UNIT if result.area is not None else None,
        length=result.length,
        length_unit=LENGTH_UNIT if result.length is not None else None,
        message=result.message,
        warnings=warnings,
    )


def _overall_measurement_crs(features: list[FeatureMeasurement]) -> Optional[str]:
    used = {f.measurement_crs for f in features if f.measurement_crs}
    if not used:
        return None
    return used.pop() if len(used) == 1 else "MULTIPLE"


def _read_dataset(extension: str, upload_path: Path, work_dir: Path, settings: Settings) -> ParsedDataset:
    if extension == ".kml":
        file_validation.validate_kml(upload_path)
        return read_kml(upload_path, settings)
    shapefile = file_validation.extract_shapefile_zip(upload_path, work_dir / "shapefile", settings)
    return read_shapefile(shapefile, settings)


def process_upload(
    stream: BinaryIO,
    original_filename: Optional[str],
    declared_size: Optional[int],
    settings: Settings,
    repository: FileRepository,
) -> UploadResponse:
    if declared_size is not None and declared_size > settings.max_upload_bytes + 64 * 1024:
        raise AppError(
            413,
            "FILE_TOO_LARGE",
            f"The upload exceeds the maximum allowed size of {settings.max_upload_bytes} bytes.",
            {"max_bytes": settings.max_upload_bytes},
        )

    filename = file_validation.sanitize_filename(original_filename)
    extension = file_validation.check_extension(filename)

    # Uploaded bytes are stored under a fixed name inside a private temp dir,
    # which is always removed - the client-supplied name never touches the disk.
    with tempfile.TemporaryDirectory(prefix="geoapi_", ignore_cleanup_errors=True) as tmp:
        work_dir = Path(tmp)
        upload_path = work_dir / f"upload{extension}"
        size = file_validation.save_upload(stream, upload_path, settings.max_upload_bytes)
        dataset = _read_dataset(extension, upload_path, work_dir, settings)

    ctx: CrsContext = build_crs_context(dataset.crs)
    features = [
        _build_feature(
            source,
            measure_geometry(source.geometry, ctx, settings.utm_max_lon_span_degrees),
        )
        for source in dataset.features
    ]

    warnings = list(dataset.warnings)
    if not features:
        warnings.append("The file contains no features.")
    if ctx.kind == "missing" and features:
        warnings.append(
            "The dataset does not declare a coordinate reference system (CRS). "
            "Areas and lengths were not calculated because the CRS is never guessed."
        )
    if ctx.kind == "unsupported":
        warnings.append(f"The source CRS cannot be used for measurement: {ctx.problem}")
    problem_count = sum(1 for f in features if f.measurement_status not in _CLEAN_STATUSES or f.warnings)
    if problem_count:
        warnings.append(
            f"{problem_count} of {len(features)} feature(s) were not fully measured or have warnings; "
            "see the per-feature 'measurement_status' and 'warnings'."
        )

    summary = dict(Counter(f.measurement_status.value for f in features))
    status = FileStatus.COMPLETED_WITH_WARNINGS if warnings else FileStatus.COMPLETED
    record = FileRecord(
        id=uuid.uuid4().hex,
        filename=filename,
        file_type=dataset.file_type,
        feature_count=len(features),
        source_crs=ctx.label,
        source_crs_name=ctx.name,
        source_crs_type=ctx.crs_type,
        measurement_crs=_overall_measurement_crs(features),
        status=status.value,
        warnings=warnings,
        summary=summary,
        size_bytes=size,
        uploaded_at=datetime.now(timezone.utc).isoformat(),
    )
    repository.save(record, [f.model_dump(mode="json") for f in features])
    return UploadResponse(
        id=record.id,
        filename=record.filename,
        feature_count=record.feature_count,
        crs=record.source_crs,
        status=status,
        warnings=warnings,
    )


def _require_file(repository: FileRepository, file_id: str) -> FileRecord:
    record = repository.get_file(file_id)
    if record is None:
        raise AppError(404, "FILE_NOT_FOUND", "No file exists with the given ID.", {"file_id": file_id[:64]})
    return record


def get_file_info(repository: FileRepository, file_id: str) -> FileInfoResponse:
    record = _require_file(repository, file_id)
    return FileInfoResponse(
        id=record.id,
        filename=record.filename,
        file_type=record.file_type,
        feature_count=record.feature_count,
        crs=record.source_crs,
        crs_name=record.source_crs_name,
        crs_type=record.source_crs_type,
        status=FileStatus(record.status),
        warnings=record.warnings,
        size_bytes=record.size_bytes,
        uploaded_at=datetime.fromisoformat(record.uploaded_at),
    )


def get_measurements(
    repository: FileRepository, file_id: str, limit: Optional[int] = None, offset: int = 0
) -> MeasurementsResponse:
    record = _require_file(repository, file_id)
    rows = repository.get_features(file_id, limit=limit, offset=offset)
    return MeasurementsResponse(
        file_id=record.id,
        filename=record.filename,
        source_crs=record.source_crs,
        measurement_crs=record.measurement_crs,
        feature_count=record.feature_count,
        offset=offset,
        returned_count=len(rows),
        summary=record.summary,
        features=[FeatureMeasurement(**row) for row in rows],
    )
