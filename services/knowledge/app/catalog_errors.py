"""Catalog error types."""
from __future__ import annotations


class TransientCatalogError(Exception):
    """Retryable ingest failure — do not commit the Kafka offset."""
