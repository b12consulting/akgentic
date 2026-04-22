"""Serialization utilities for actor messages.

Provides SerializableBaseModel base class and serialization functions
for proper handling of UUID, datetime, ActorAddress, and nested Pydantic models.

Source: Preserves v1 serialization behavior from akgentic-framework.
"""

from __future__ import annotations

import base64
import uuid
from dataclasses import fields, is_dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, ValidationInfo, model_serializer, model_validator
from pydantic_core import to_jsonable_python

from akgentic.core.actor_address import ActorAddress
from akgentic.core.utils.deserializer import ActorAddressDict, deserialize_object

if TYPE_CHECKING:
    from collections.abc import Callable

    # Imported only for type hints; the runtime import happens lazily inside
    # ``_hydrate_value``/``_snapshot_value`` to avoid circular imports with
    # ``actor_address_impl``.
    from akgentic.core.actor_address_impl import ActorAddressProxy

    # Type alias for the address resolver callable passed to
    # :func:`hydrate_addresses`. Only defined under ``TYPE_CHECKING`` because
    # ``ActorAddressProxy`` is lazy-imported; all uses below appear in
    # annotations that are strings under ``from __future__ import annotations``.
    AddressResolver = Callable[[ActorAddressProxy], ActorAddress]


def serialize_type(value: type[Any] | Any) -> str:
    """Serialize a type to its module path and name.

    Args:
        value: A type or instance to serialize.

    Returns:
        String in format "module.ClassName".
    """
    if isinstance(value, type):
        return f"{value.__module__}.{value.__name__}"
    return f"{value.__class__.__module__}.{value.__class__.__name__}"


def serialize(value: Any) -> dict[str, Any] | list[Any] | str | None | ActorAddressDict:
    """Recursively serialize values, adding tagged-dict metadata where needed.

    Handles UUID, datetime, ActorAddress, bytes, types, lists, dicts, BaseModel,
    and dataclasses with proper serialization. Special tagged dicts:
    - ``__model__``: Pydantic model or plain dataclass class path
    - ``__type__``: Python type class path
    - ``__bytes__``: Base64-encoded binary data

    Args:
        value: The value to serialize.

    Returns:
        Serialized representation of the value.
    """
    if value is None:
        return None
    elif isinstance(value, uuid.UUID):
        return str(value)
    elif isinstance(value, ActorAddress):
        return value.serialize()
    elif isinstance(value, datetime):
        return value.isoformat()
    elif isinstance(value, bytes):
        return {"__bytes__": base64.b64encode(value).decode("ascii")}
    elif isinstance(value, type):
        return {"__type__": serialize_type(value)}
    elif isinstance(value, (list, set, tuple)):
        return [serialize(item) for item in value]
    elif isinstance(value, dict):
        return {k: serialize(v) for k, v in value.items()}
    elif isinstance(value, BaseModel):
        model_dict: dict[str, Any] = value.model_dump()
        return model_dict
    elif is_dataclass(value) and not isinstance(value, type):
        result: dict[str, Any] = {}
        for f in fields(value):
            result[f.name] = serialize(getattr(value, f.name))
        result["__model__"] = serialize_type(value)
        return result
    else:
        return to_jsonable_python(value)  # type: ignore[no-any-return]


def get_field_serializers_map(model_class: type[BaseModel]) -> dict[str, Any]:
    """Extract field serializers from a Pydantic model class.

    Args:
        model_class: The Pydantic model class to inspect.

    Returns:
        Dictionary mapping field names to their serializer functions.
    """
    field_serializers_map: dict[str, Any] = {}
    decorators = getattr(model_class, "__pydantic_decorators__", None)
    if decorators and hasattr(decorators, "field_serializers"):
        for decorator_info in decorators.field_serializers.values():
            for field_name in decorator_info.info.fields:
                field_serializers_map[field_name] = decorator_info.func
    return field_serializers_map


def serialize_base_model(cls: BaseModel) -> dict[str, Any]:
    """Add __model__ metadata after field serialization.

    Args:
        cls: The BaseModel instance to serialize.

    Returns:
        Dictionary with serialized fields and __model__ metadata.
    """
    data: dict[str, Any] = {}
    field_serializers_map = get_field_serializers_map(cls.__class__)

    for field_name, field_info in cls.__class__.model_fields.items():
        if field_info.exclude:
            continue
        value = getattr(cls, field_name, None)

        if field_name in field_serializers_map:
            serializer_func = field_serializers_map[field_name]
            value = serializer_func(cls, value)

        data[field_name] = serialize(value)

    data["__model__"] = serialize_type(cls)
    return data


def _snapshot_value(value: Any) -> Any:  # noqa: ANN401
    """Recursively snapshot a single value, replacing live actor addresses.

    Handles ``ActorAddressImpl``, ``BaseModel``, ``list``, ``tuple``,
    ``set``, and ``dict`` values.  Returns the original object unchanged
    when no replacements are needed.
    """
    from akgentic.core.actor_address_impl import ActorAddressImpl, ActorAddressProxy

    if isinstance(value, ActorAddressImpl):
        return ActorAddressProxy(value.serialize())
    if isinstance(value, BaseModel):
        return snapshot_addresses(value)
    if isinstance(value, (list, tuple, set)):
        items = [_snapshot_value(item) for item in value]
        if all(new is orig for new, orig in zip(items, value)):
            return value
        return type(value)(items)
    if isinstance(value, dict):
        new_dict = {k: _snapshot_value(v) for k, v in value.items()}
        if all(new_dict[k] is v for k, v in value.items()):
            return value
        return new_dict
    return value


def snapshot_addresses(model: BaseModel) -> BaseModel:
    """Replace live ``ActorAddressImpl`` references with ``ActorAddressProxy`` snapshots.

    Walks every Pydantic field on the concrete model class and recursively
    processes values — including nested ``BaseModel`` instances, ``list``,
    ``dict``, ``tuple``, and ``set`` containers.  Returns the original
    instance unchanged when no replacements are needed.

    Args:
        model: A Pydantic ``BaseModel`` instance (typically a ``Message``).

    Returns:
        The original model if no live addresses were found, or a shallow
        ``model_copy`` with all ``ActorAddressImpl`` values replaced.
    """
    updates: dict[str, Any] = {}
    for name in type(model).model_fields:
        value = getattr(model, name)
        snapshotted = _snapshot_value(value)
        if snapshotted is not value:
            updates[name] = snapshotted
    if updates:
        return model.model_copy(update=updates)
    return model


def _hydrate_value(value: Any, resolver: AddressResolver) -> Any:  # noqa: ANN401
    """Recursively hydrate a single value, replacing proxy addresses via *resolver*.

    Mirror image of :func:`_snapshot_value`.  Handles ``ActorAddressProxy``,
    ``BaseModel``, ``list``, ``tuple``, ``set``, and ``dict`` values.  Returns
    the original object unchanged when no replacements are needed (identity
    preservation) so that the top-level ``hydrate_addresses`` can skip the
    ``model_copy`` entirely.
    """
    from akgentic.core.actor_address_impl import ActorAddressProxy

    if isinstance(value, ActorAddressProxy):
        return resolver(value)
    if isinstance(value, BaseModel):
        return hydrate_addresses(value, resolver)
    if isinstance(value, (list, tuple, set)):
        items = [_hydrate_value(item, resolver) for item in value]
        if all(new is orig for new, orig in zip(items, value)):
            return value
        return type(value)(items)
    if isinstance(value, dict):
        new_dict = {k: _hydrate_value(v, resolver) for k, v in value.items()}
        if all(new_dict[k] is v for k, v in value.items()):
            return value
        return new_dict
    return value


def hydrate_addresses(model: BaseModel, resolver: AddressResolver) -> BaseModel:
    """Replace ``ActorAddressProxy`` references with live addresses via *resolver*.

    Inverse of :func:`snapshot_addresses`.  Walks every Pydantic field on the
    concrete model class and recursively processes nested values (``BaseModel``,
    ``list``, ``dict``, ``tuple``, ``set``).  Returns the original instance
    unchanged — by identity — when no replacements are needed, so callers can
    use ``result is model`` as a fast-path check.

    Args:
        model: A Pydantic ``BaseModel`` instance (typically a ``Message``)
            carrying potentially deserialized proxy addresses.
        resolver: Callable that maps an ``ActorAddressProxy`` to a live
            ``ActorAddress``.  Typically closes over an orchestrator proxy or
            other runtime-layer registry that tracks live actors.

    Returns:
        The original model if no proxies were found, or a shallow
        ``model_copy`` with all proxy addresses replaced by the resolver's
        return values.

    Raises:
        Exception: Whatever *resolver* raises when it cannot resolve a proxy
            — the exception propagates unchanged, and the original ``model``
            argument is left untouched.

    Example:
        >>> def resolver(proxy: ActorAddressProxy) -> ActorAddress:
        ...     return orchestrator_registry[proxy.agent_id]
        >>> live_msg = hydrate_addresses(message, resolver)
    """
    updates: dict[str, Any] = {}
    for name in type(model).model_fields:
        value = getattr(model, name)
        hydrated = _hydrate_value(value, resolver)
        if hydrated is not value:
            updates[name] = hydrated
    if updates:
        return model.model_copy(update=updates)
    return model


class SerializableBaseModel(BaseModel):
    """Base class for models that need proper serialization of UUID, ActorAddress, etc.

    Provides automatic serialization and deserialization of complex types
    including UUID, datetime, ActorAddress, and nested Pydantic models.

    Attributes:
        model_config: Pydantic configuration allowing arbitrary types.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @model_validator(mode="before")
    @classmethod
    def deserialize_types(cls, data: Any, info: ValidationInfo) -> Any:
        """Deserialize custom type representations.

        Args:
            data: Input data to deserialize.
            info: Pydantic validation info with context.

        Returns:
            Deserialized data suitable for model construction.
        """
        context = info.context if info.context else None
        module_path_name = serialize_type(cls)
        if isinstance(data, dict) and data.get("__model__") == module_path_name:
            return {
                key: deserialize_object(value, context)
                for key, value in data.items()
                if key != "__model__"
            }
        return deserialize_object(data, context)

    @model_serializer
    def serialize_model(self) -> dict[str, Any]:
        """Serialize with proper handling of UUID, ActorAddress, datetime, and nested models.

        Returns:
            Dictionary with serialized fields and __model__ metadata.
        """
        return serialize_base_model(self)
