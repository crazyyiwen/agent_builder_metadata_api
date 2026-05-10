"""Repository / service exception types.

Repos and services raise domain-shaped exceptions that the API layer
translates to HTTP status codes. Routes never see PyMongo errors directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.common import ValidationIssue


class RepositoryError(Exception):
    """Base for all repo/service-layer errors."""


class NotFoundError(RepositoryError):
    """The requested resource does not exist."""


class ConflictError(RepositoryError):
    """Optimistic-concurrency conflict, duplicate key, or other 409-shaped failure."""


class ValidationError(RepositoryError):
    """A pre-persistence validation guard failed (distinct from Pydantic
    schema errors at the API boundary). Carries the per-rule issues so the
    API layer can return them verbatim."""

    def __init__(
        self,
        message: str,
        *,
        issues: "list[ValidationIssue] | None" = None,
    ) -> None:
        super().__init__(message)
        self.issues = list(issues or [])


class OperationNotAllowedError(RepositoryError):
    """The requested operation is disabled by configuration (e.g. hard
    delete is off)."""
