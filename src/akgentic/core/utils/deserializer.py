"""Deserialization utilities for actor messages.

Provides DeserializeContext abstract class and deserialize_object function
for reconstructing serialized messages with proper type handling.

Source: Preserves v1 deserialization behavior from akgentic-framework.
"""

from __future__ import annotations

import base64
import uuid
from abc import ABC, abstractmethod
from dataclasses import is_dataclass
from importlib import import_module
from typing import Any, TypedDict, cast

from pydantic import PydanticUserError, TypeAdapter


class ActorAddressDict(TypedDict):
    """TypedDict for serialized ActorAddress representation.

    Contains all metadata fields needed for Phase 3 remote communication
    and address reconstruction during deserialization.

    Attributes:
        __actor_address__: Marker for deserialization dispatch.
        __actor_type__: Fully qualified class name for reconstruction.
        agent_id: UUID as string for unique agent identification.
        name: Agent name from configuration.
        role: Agent role from configuration.
        team_id: Team UUID as string for team identification.
        squad_id: Squad UUID as string for squad identification.
        user_message: Whether agent accepts user messages.
    """

    __actor_address__: bool
    __actor_type__: str
    agent_id: str
    name: str
    role: str
    team_id: str
    squad_id: str
    user_message: bool


class DeserializeContext(ABC):
    """Abstract base class for deserialization context.

    Provides interface for resolving ActorAddress references during
    message deserialization. Implementations handle address resolution
    based on the specific actor system configuration.
    """

    @abstractmethod
    def resolve_address(self, address_dict: ActorAddressDict) -> Any:
        """Resolve an address_dict to an ActorAddress.

        Args:
            address_dict: Serialized address dictionary with id and name.

        Returns:
            Resolved ActorAddress instance.
        """
        ...


def import_class(class_path: str) -> type[Any]:
    """Dynamically import a class from the given path.

    Args:
        class_path: Full module path and class name (e.g., "module.ClassName").

    Returns:
        The imported class.

    Raises:
        ImportError: If module cannot be imported.
        AttributeError: If class not found in module.
    """
    module_path, class_name = class_path.rsplit(".", 1)
    module = import_module(module_path)
    return getattr(module, class_name)  # type: ignore[no-any-return]


_type_adapter_cache: dict[type[Any], TypeAdapter[Any] | None] = {}

def _get_type_adapter(cls: type[Any]) -> TypeAdapter[Any] | None:
    """Return a cached TypeAdapter for *cls*, or None if the type is not fully defined."""
    try:
        return _type_adapter_cache[cls]
    except KeyError:
        try:
            ta: TypeAdapter[Any] | None = TypeAdapter(cls)
        except Exception:
            # TypeAdapter fails when annotations are unresolved
            # (e.g. TYPE_CHECKING imports with `from __future__ import annotations`).
            ta = None
        _type_adapter_cache[cls] = ta
        return ta


def is_uuid_canonical(val: Any) -> bool:
    """Check if string is valid UUID in canonical format.

    Args:
        val: Value to check.

    Returns:
        True if val is a string in canonical UUID format (36 chars with hyphens).
    """
    if not isinstance(val, str) or len(val) != 36:
        return False
    try:
        return str(uuid.UUID(val)) == val
    except ValueError:
        return False


def deserialize_object(
    obj: dict[str, Any] | list[Any] | set[Any] | tuple[Any, ...] | Any,
    context: DeserializeContext | None = None,
    canonical_uuid: bool = False,
) -> Any:
    """Recursively deserialize Pydantic models from dictionaries containing tagged-dict markers.

    Handles special markers:
    - __actor_address__: Reconstructs ActorAddress via context or proxy
    - __bytes__: Decodes base64 string back to raw bytes
    - __type__: Imports and returns the type
    - __model__: Reconstructs Pydantic model or dataclass

    Args:
        obj: Object to deserialize (dict, list, set, tuple, or primitive).
        context: Optional deserialization context for address resolution.
        canonical_uuid: If True, convert canonical UUID strings to uuid.UUID.

    Returns:
        Deserialized object with proper types restored.

    Raises:
        ValueError: If model deserialization fails.
    """
    if isinstance(obj, dict):
        if "__actor_address__" in obj:
            address_dict = cast(ActorAddressDict, obj)
            if context is None:
                # Import here to avoid circular imports at module level
                from akgentic.core.actor_address_impl import ActorAddressProxy

                return ActorAddressProxy(address_dict)
            return context.resolve_address(address_dict)

        if "__bytes__" in obj:
            return base64.b64decode(obj["__bytes__"])

        if "__type__" in obj:
            return import_class(obj["__type__"])

        if "__model__" in obj:
            model_class = import_class(obj["__model__"])
            deserialized_data = {
                key: deserialize_object(value, context)
                for key, value in obj.items()
                if key != "__model__"
            }
            try:
                if is_dataclass(model_class) and (adapter := _get_type_adapter(model_class)):
                    # Dataclasses: use TypeAdapter for proper type coercion
                    # (datetime from ISO strings, nested unions, etc.).
                    # Falls back to direct construction when annotations are
                    # unresolvable (e.g. TYPE_CHECKING-guarded imports).
                    try:
                        model: object = adapter.validate_python(deserialized_data)
                    except PydanticUserError:
                        model = model_class(**deserialized_data)
                else:
                    # BaseModel: coercion handled by Pydantic validators.
                    model = model_class(**deserialized_data)
            except Exception as e:
                raise ValueError(
                    f"Error deserializing model {model_class}: {e}\nData: {deserialized_data}"
                ) from e
            return model

        return {key: deserialize_object(value, context) for key, value in obj.items()}

    elif isinstance(obj, list):
        return [deserialize_object(item, context) for item in obj]

    elif isinstance(obj, set):
        return {deserialize_object(item, context) for item in obj}

    elif isinstance(obj, tuple):
        return tuple(deserialize_object(item, context) for item in obj)

    elif canonical_uuid and is_uuid_canonical(obj):
        return uuid.UUID(obj)

    else:
        return obj
