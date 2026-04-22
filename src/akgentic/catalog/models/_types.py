"""Shared type aliases for catalog models."""

from __future__ import annotations

from typing import Annotated

from pydantic import StringConstraints

# Enforces non-empty strings on catalog identifiers and required name fields
NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]

__all__ = ["NonEmptyStr"]
