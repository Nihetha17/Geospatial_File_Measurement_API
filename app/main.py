"""Application factory.

Run with:  uvicorn app.main:app --reload
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI

from app import __version__
from app.api.routes import files, health
from app.core.config import Settings
from app.core.error_handlers import register_error_handlers
from app.repositories.file_repository import FileRepository


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.repository.init_db()  # creates the SQLite file/tables if needed
    yield


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=(
            "Upload KML or zipped Shapefile datasets and get per-feature area (m²) and length (m) "
            "calculated in a suitable projected CRS."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.repository = FileRepository(settings.database_path)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(files.router, prefix="/api/files", tags=["files"])
    return app


app = create_app()
