"""Application exceptions.

Every *expected* failure is raised as ``AppError``.  The error handlers in
``error_handlers.py`` turn it into the API's consistent JSON error format.
"""
from __future__ import annotations

from typing import Any, Optional


class AppError(Exception):
    """An expected, client-facing error with a stable machine-readable code."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
