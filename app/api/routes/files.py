from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile

from app.api.dependencies import get_repository, get_settings
from app.core.config import Settings
from app.repositories.file_repository import FileRepository
from app.schemas.error import ErrorResponse
from app.schemas.file import FileInfoResponse, UploadResponse
from app.schemas.measurement import MeasurementsResponse
from app.services import file_service

router = APIRouter()

_UPLOAD_ERRORS = {
    400: {"model": ErrorResponse, "description": "Invalid/empty file, invalid ZIP or unsafe archive."},
    413: {"model": ErrorResponse, "description": "File, archive or feature count exceeds a configured limit."},
    415: {"model": ErrorResponse, "description": "Unsupported file extension."},
    422: {"model": ErrorResponse, "description": "Corrupt KML/Shapefile or missing Shapefile components."},
}


@router.post(
    "/",
    response_model=UploadResponse,
    status_code=201,
    summary="Upload a KML file or a ZIP containing a Shapefile",
    responses=_UPLOAD_ERRORS,
)
def upload_file(
    request: Request,
    file: UploadFile = File(..., description="A .kml file, or a .zip containing one Shapefile (.shp/.shx/.dbf[/.prj])."),
    settings: Settings = Depends(get_settings),
    repository: FileRepository = Depends(get_repository),
) -> UploadResponse:
    declared = request.headers.get("content-length")
    declared_size = int(declared) if declared and declared.isdigit() else None
    return file_service.process_upload(file.file, file.filename, declared_size, settings, repository)


@router.get(
    "/{file_id}/",
    response_model=FileInfoResponse,
    summary="Get information about an uploaded file",
    responses={404: {"model": ErrorResponse}},
)
def get_file(file_id: str, repository: FileRepository = Depends(get_repository)) -> FileInfoResponse:
    return file_service.get_file_info(repository, file_id)


@router.get(
    "/{file_id}/measurements/",
    response_model=MeasurementsResponse,
    summary="Get per-feature measurements",
    responses={404: {"model": ErrorResponse}},
)
def get_measurements(
    file_id: str,
    limit: Optional[int] = Query(default=None, ge=1, le=100_000, description="Max features to return (default: all)."),
    offset: int = Query(default=0, ge=0, description="Number of features to skip."),
    repository: FileRepository = Depends(get_repository),
) -> MeasurementsResponse:
    return file_service.get_measurements(repository, file_id, limit=limit, offset=offset)
