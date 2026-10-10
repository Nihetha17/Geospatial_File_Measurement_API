from __future__ import annotations

from fastapi import APIRouter

from app import __version__

router = APIRouter()


@router.get("/health", tags=["health"], summary="Health check")
def health() -> dict[str, str]:
    """Confirms the service is running."""
    return {"status": "ok", "service": "geospatial-file-measurement-api", "version": __version__}


@router.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"message": "Geospatial File Measurement API", "docs": "/docs", "health": "/health"}
