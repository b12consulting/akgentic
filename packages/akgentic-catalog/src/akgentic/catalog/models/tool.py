"""ToolEntry model for tool configuration catalog entries."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_serializer, model_validator

from akgentic.catalog.models._types import NonEmptyStr
from akgentic.core.utils.deserializer import import_class
from akgentic.tool import ToolCard


class ToolEntry(BaseModel):
    """A tool configuration catalog entry with dynamic class resolution."""

    id: NonEmptyStr = Field(description="Unique catalog identifier for this tool")
    tool_class: NonEmptyStr = Field(
        description="Fully qualified Python class path for the ToolCard subclass"
    )
    tool: ToolCard = Field(
        description="Tool configuration validated against the resolved tool_class"
    )

    @model_validator(mode="before")
    @classmethod
    def resolve_tool(cls, data: Any) -> Any:  # noqa: ANN401
        """Resolve tool_class to the concrete ToolCard subclass and validate.

        Imports the class at ``tool_class``, then validates the ``tool``
        dict against the resolved subclass's Pydantic schema — enables
        deserialization of abstract ``ToolCard`` from YAML without
        knowing the concrete type at parse time.

        Args:
            data: Raw input data (typically a dict from YAML deserialization).

        Returns:
            The data dict with ``tool`` replaced by a validated instance
            of the ``ToolCard`` subclass resolved from ``tool_class``.

        Raises:
            ValueError: If ``tool_class`` cannot be imported.
        """
        if not isinstance(data, dict):
            return data
        if isinstance(data.get("tool"), dict):
            tool_class_path = data.get("tool_class")
            if not isinstance(tool_class_path, str):
                return data
            try:
                klass = import_class(tool_class_path)
            except (ImportError, AttributeError) as e:
                raise ValueError(f"Cannot resolve tool_class '{tool_class_path}': {e}") from e
            data["tool"] = klass.model_validate(data["tool"])
        return data

    @model_serializer(mode="wrap")
    def serialize_model(self, handler: Any) -> dict[str, Any]:  # noqa: ANN401
        """Serialize tool field against runtime type to preserve custom ToolCard subclass fields.

        Pydantic v2 serializes the ``tool`` field against the declared
        ``ToolCard`` annotation, which only includes ``name`` and
        ``description``.  Using ``mode='wrap'`` delegates to the default
        handler for all fields, then overrides ``tool`` by calling
        ``model_dump()`` on the runtime instance to capture subclass fields.
        """
        data: dict[str, Any] = handler(self)
        data["tool"] = self.tool.model_dump()
        return data


__all__ = ["ToolEntry"]
