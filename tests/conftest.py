from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Settings pointing at a throw-away SQLite database."""
    return Settings(database_path=tmp_path / "test.db")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def make_client():
    """Factory for clients with custom settings (limits, other DB, restarts)."""
    opened: list = []

    def _make(settings: Settings) -> TestClient:
        context = TestClient(create_app(settings))
        opened.append(context)
        return context.__enter__()

    yield _make
    for context in reversed(opened):
        context.__exit__(None, None, None)
