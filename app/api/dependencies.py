"""FastAPI dependencies giving routes access to settings and the repository."""
from __future__ import annotations

from fastapi import Request

from app.core.config import Settings
from app.repositories.file_repository import FileRepository


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_repository(request: Request) -> FileRepository:
    return request.app.state.repository
