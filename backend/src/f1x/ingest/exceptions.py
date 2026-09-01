"""Errors raised while retrieving or validating source timing data."""

from __future__ import annotations


class IngestionError(RuntimeError):
    """A source session could not be loaded or stored safely."""


class DataQualityError(IngestionError):
    """Source data failed a gate and must not be materialised."""
