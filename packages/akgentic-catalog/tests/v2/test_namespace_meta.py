"""Tests for ``NamespaceMeta`` — the payload model for the ``kind="meta"`` entry.

Story 17.7 / AC17 — pin the typed ``shareable: bool`` root field contract:

* default ``False``, accepts ``True`` / ``False``, round-trips through
  ``model_dump`` with the key present;
* Pydantic strict-mode rejects non-bool inputs (e.g. the string ``"true"``)
  with a ``ValidationError`` — no truthy-string coercion at the model layer.

The model also pins that ``properties`` is fully free-form ``str -> str``
with NO catalog-reserved keys (AC2): a caller may supply
``properties["shared"] = "true"`` as plain string data and Pydantic accepts
it without complaint, but the catalog gate / route projection / serializer
header consult ``payload["shareable"]`` only.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from akgentic.catalog.models.namespace_meta import NamespaceMeta


class TestNamespaceMetaShareableField:
    """AC17 — ``shareable: bool`` root-field contract."""

    def test_default_shareable_is_false(self) -> None:
        meta = NamespaceMeta(name="x")
        assert meta.shareable is False
        # AC1 — model_dump of a default-constructed instance carries
        # ``shareable: False`` (no special opcode / exclude flag required).
        dumped = meta.model_dump()
        assert dumped["shareable"] is False

    def test_explicit_shareable_true_round_trip(self) -> None:
        meta = NamespaceMeta(name="x", shareable=True)
        assert meta.shareable is True
        dumped = meta.model_dump()
        assert dumped["shareable"] is True

    def test_explicit_shareable_false_round_trip(self) -> None:
        meta = NamespaceMeta(name="x", shareable=False)
        assert meta.shareable is False
        dumped = meta.model_dump()
        assert dumped["shareable"] is False

    def test_pydantic_rejects_non_bool_string(self) -> None:
        # AC1 — strict-mode rejection of non-bool input. Pydantic's default
        # bool validation is permissive of true/false-like strings; we pin
        # that ``"true"`` is rejected via ``model_validate(strict=True)`` —
        # the catalog write pipeline runs validation at construction so an
        # operator cannot smuggle a string through ``payload["shareable"]``
        # at the NamespaceMeta layer.
        with pytest.raises(ValidationError):
            NamespaceMeta.model_validate(
                {"name": "x", "shareable": "true"},
                strict=True,
            )

    def test_pydantic_rejects_int_under_strict(self) -> None:
        with pytest.raises(ValidationError):
            NamespaceMeta.model_validate(
                {"name": "x", "shareable": 1},
                strict=True,
            )


class TestNamespaceMetaFieldOrder:
    """Story 18.2 AC1 — five declared fields in pinned order.

    Order matters: it pins the export-bundle header projection order
    (architecture §07-api emits header keys by declaration order — see
    Story 17.7 AC8 and Story 18.2 AC3).
    """

    def test_model_fields_declaration_order(self) -> None:
        assert list(NamespaceMeta.model_fields.keys()) == [
            "name",
            "description",
            "properties",
            "shareable",
            "public",
        ]


class TestNamespaceMetaPublicField:
    """Story 18.2 AC1 — ``public: bool`` root-field contract."""

    def test_default_public_is_false(self) -> None:
        meta = NamespaceMeta(name="x")
        assert meta.public is False
        dumped = meta.model_dump()
        assert dumped["public"] is False

    def test_explicit_public_true_round_trip(self) -> None:
        meta = NamespaceMeta(name="x", public=True)
        assert meta.public is True
        dumped = meta.model_dump()
        assert dumped["public"] is True
        # Round-trip via model_validate preserves the typed bool.
        rebuilt = NamespaceMeta.model_validate(dumped)
        assert rebuilt.public is True
        assert rebuilt.model_dump() == dumped

    def test_explicit_public_false_round_trip(self) -> None:
        meta = NamespaceMeta(name="x", public=False)
        assert meta.public is False
        dumped = meta.model_dump()
        assert dumped["public"] is False

    def test_pydantic_rejects_non_bool_string(self) -> None:
        # Strict-mode rejection mirrors ``shareable``'s contract — operators
        # must opt in with a real boolean. Error message must include the
        # field name so error envelopes route the user back to ``public``.
        with pytest.raises(ValidationError) as excinfo:
            NamespaceMeta.model_validate(
                {"name": "x", "public": "true"},
                strict=True,
            )
        assert "public" in str(excinfo.value)

    def test_pydantic_rejects_int_under_strict(self) -> None:
        with pytest.raises(ValidationError):
            NamespaceMeta.model_validate(
                {"name": "x", "public": 1},
                strict=True,
            )

    def test_dump_preserves_field_order(self) -> None:
        # AC1 — ``model_dump()`` ordering matches declaration order so the
        # YAML wire format keeps ``public`` immediately after ``shareable``.
        meta = NamespaceMeta(name="x", public=True, shareable=True)
        keys = list(meta.model_dump().keys())
        assert keys == ["name", "description", "properties", "shareable", "public"]


class TestNamespaceMetaPropertiesFreeForm:
    """AC2 — ``properties`` is fully free-form ``str -> str`` with NO reserved keys."""

    def test_properties_accepts_arbitrary_string_keys(self) -> None:
        # ``properties["shared"]`` is plain string data: Pydantic accepts
        # it because the field type is ``dict[str, str]``. Only the catalog
        # gate / route projection / serializer consult ``payload["shareable"]``.
        meta = NamespaceMeta(
            name="x",
            properties={"shared": "true", "owner_team": "platform"},
        )
        assert meta.properties == {"shared": "true", "owner_team": "platform"}
        # Default ``shareable`` is still False — ``properties["shared"]`` is
        # plain data and does NOT influence the typed root field.
        assert meta.shareable is False

    def test_properties_rejects_non_string_value(self) -> None:
        # ``dict[str, str]`` is enforced at the Pydantic layer.
        with pytest.raises(ValidationError):
            NamespaceMeta.model_validate(
                {"name": "x", "properties": {"k": 42}},
                strict=True,
            )
