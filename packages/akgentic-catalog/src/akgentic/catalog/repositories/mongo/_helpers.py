"""Shared helpers for MongoDB document ↔ Pydantic model mapping.

Converts between MongoDB's _id convention and the model's id field so that
all four catalog repositories can share the same round-trip logic.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


def to_document(entry: BaseModel) -> dict[str, Any]:
    """Convert a Pydantic model to a MongoDB document.

    Calls model_dump(), removes the ``id`` key, and stores its value as
    ``_id`` so MongoDB uses the catalog id as the primary key.

    Args:
        entry: A Pydantic model instance with an ``id`` field.

    Returns:
        A dict suitable for MongoDB insert/replace operations.
    """
    doc = entry.model_dump()
    id_value = doc.pop("id", None)
    if id_value is not None:
        doc["_id"] = id_value
    logger.debug("to_document: mapped id=%s to _id", id_value)
    return doc


def from_document[T: BaseModel](doc: dict[str, Any], entry_type: type[T]) -> T:
    """Reconstruct a Pydantic model from a MongoDB document.

    Pops ``_id`` from the document and sets it as ``id``, then validates
    through the model constructor.

    Args:
        doc: A MongoDB document dict (may contain ``_id``).
        entry_type: The Pydantic model class to validate into.

    Returns:
        A validated instance of entry_type.
    """
    data = dict(doc)  # shallow copy to avoid mutating the original
    id_value = data.pop("_id", None)
    if id_value is not None and "id" not in data:
        data["id"] = id_value
    logger.debug("from_document: mapped _id=%s to id", id_value)
    return entry_type.model_validate(data)


__all__ = ["from_document", "to_document"]
