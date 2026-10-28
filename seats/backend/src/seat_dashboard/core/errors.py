from __future__ import annotations


class SnapshotUnavailable(RuntimeError):
    """Raised when no complete published local snapshot is available."""


class SnapshotValidationError(RuntimeError):
    """Raised when a candidate snapshot fails before publication."""


class InvalidDashboardQuery(ValueError):
    """Raised for invalid user-controlled query parameters."""
