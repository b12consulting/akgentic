"""Catalog @-reference utilities.

Handles the catalog reference convention where ``@entry-id`` in
``PromptTemplate.template`` fields points to another catalog entry.

IMPORTANT: Two ``@`` conventions coexist — ``config.name`` uses ``@`` for
agent routing names; ``prompt.template`` uses ``@`` for catalog references.
These functions must ONLY be applied to ``PromptTemplate.template`` fields,
never to ``config.name`` or ``routes_to`` values.
"""

from __future__ import annotations

__all__ = [
    "_is_catalog_ref",
    "_resolve_ref",
]


def _is_catalog_ref(value: str) -> bool:
    """Check if a string is a catalog @-reference.

    ONLY call this on PromptTemplate.template fields.
    Never on config.name or routes_to values.

    Args:
        value: The string to check (must be a ``PromptTemplate.template``
            field value, never a ``config.name`` or ``routes_to`` value).

    Returns:
        True if the string starts with ``@``, indicating a catalog reference.
    """
    return value.startswith("@")


def _resolve_ref(value: str) -> str:
    """Strip the leading ``@`` prefix to extract the catalog entry id.

    Args:
        value: A catalog @-reference string (e.g. ``@my-template``).

    Returns:
        The entry id without the ``@`` prefix (e.g. ``my-template``).
    """
    return value[1:]
