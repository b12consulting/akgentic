"""Tests for the v2 ``Entry`` model, ``EntryKind``, and ``AllowlistedPath``."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from akgentic.catalog.models.entry import (
    AllowlistedPath,
    Entry,
    EntryKind,
    NonEmptyStr,
)

from .conftest import make_entry

# Reference the re-exports so the imports are not flagged as unused; also doubles
# as a smoke-level AC1 check that the names resolve to usable objects.
assert NonEmptyStr is not None
assert AllowlistedPath is not None
assert EntryKind is not None  # type: ignore[truthy-function]


class TestEntryImport:
    """AC1 — public importability of the v2 entry-model surface."""

    def test_entry_is_base_model_subclass(self) -> None:
        assert issubclass(Entry, BaseModel)

    def test_entry_kind_alias_has_expected_literal_values(self) -> None:
        # ``Literal`` aliases expose their members via typing.get_args
        from typing import get_args

        assert set(get_args(EntryKind)) == {
            "team",
            "agent",
            "tool",
            "model",
            "prompt",
            "meta",
        }

    def test_entry_kind_accepts_meta_literal(self) -> None:
        """AC2 — Story 17.2: Entry construction accepts ``kind="meta"``."""
        entry = Entry.model_validate(
            {
                "id": "_meta",
                "kind": "meta",
                "namespace": "tenant-42",
                "model_type": "akgentic.catalog.models.namespace_meta.NamespaceMeta",
                "payload": {"name": "Tenant 42"},
            }
        )
        assert entry.kind == "meta"
        assert entry.id == "_meta"


class TestEntryFields:
    """AC7 — Entry.model_fields exposes exactly the specified fields."""

    def test_field_set_is_exact(self) -> None:
        expected_fields = {
            "id",
            "kind",
            "namespace",
            "user_id",
            "model_type",
            "description",
            "payload",
        }
        assert set(Entry.model_fields.keys()) == expected_fields

    @pytest.mark.parametrize(
        "field_name",
        ["id", "kind", "namespace", "model_type", "payload"],
    )
    def test_required_fields(self, field_name: str) -> None:
        assert Entry.model_fields[field_name].is_required()

    @pytest.mark.parametrize(
        ("field_name", "expected_default"),
        [
            ("user_id", "anonymous"),
            ("description", ""),
        ],
    )
    def test_optional_field_defaults(self, field_name: str, expected_default: object) -> None:
        field = Entry.model_fields[field_name]
        assert not field.is_required()
        assert field.default == expected_default

    def test_every_field_has_description(self) -> None:
        missing = [name for name, f in Entry.model_fields.items() if not f.description]
        assert not missing, f"fields without description: {missing}"


class TestAllowlistedPath:
    """AC9 — ``AllowlistedPath`` prefix enforcement."""

    @pytest.mark.parametrize(
        "path",
        [
            "akgentic.llm.ModelConfig",
            "akgentic.core.agent_card.AgentCard",
            "akgentic.tool.search.SearchTool",
        ],
    )
    def test_accepts_akgentic_prefixed_paths(self, path: str) -> None:
        entry = make_entry(model_type=path)
        assert entry.model_type == path

    @pytest.mark.parametrize(
        "path",
        [
            "os.system",
            "builtins.eval",
            "",
            "akgentic",  # missing trailing dot — prefix check is strict
            "akgenticfake.Impersonator",
        ],
    )
    def test_rejects_non_allowlisted_paths(self, path: str) -> None:
        with pytest.raises(ValidationError) as exc_info:
            make_entry(model_type=path)
        assert "outside allowlist" in str(exc_info.value)


class TestNamespaceRequired:
    """The ``namespace`` field rejects None and empty string via ``NonEmptyStr``."""

    def test_none_namespace_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Entry(
                id="entry-1",
                kind="tool",
                namespace=None,  # type: ignore[arg-type]
                model_type="akgentic.core.agent_card.AgentCard",
                payload={},
            )

    def test_empty_namespace_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Entry(
                id="entry-1",
                kind="tool",
                namespace="",
                model_type="akgentic.core.agent_card.AgentCard",
                payload={},
            )


class TestNamespaceDotForbidden:
    """Story 17.3 / AC5 — ``Entry.namespace`` must not contain ``.``."""

    @pytest.mark.parametrize(
        "bad_ns",
        ["my.ns", "global.shared", "a.b.c", "."],
    )
    def test_dot_in_namespace_rejected(self, bad_ns: str) -> None:
        with pytest.raises(ValidationError) as exc_info:
            make_entry(namespace=bad_ns)
        assert "must not contain '.'" in str(exc_info.value)

    def test_dotted_id_still_accepted(self) -> None:
        """``Entry.id`` continues to allow dots — only ``namespace`` is restricted."""
        entry = make_entry(namespace="ok-ns", id="x.y.z")
        assert entry.id == "x.y.z"
        assert entry.namespace == "ok-ns"


class TestUserIdAnonymousDefault:
    """Story 18.1 / AC1 — ``Entry.user_id`` is required ``str`` defaulting to ``"anonymous"``."""

    def test_user_id_default_is_anonymous(self) -> None:
        entry = make_entry()
        assert entry.user_id == "anonymous"

    def test_user_id_none_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            make_entry(user_id=None)
        assert "user_id" in str(exc_info.value)

    def test_user_id_empty_string_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            make_entry(user_id="")
        assert "user_id" in str(exc_info.value)

    def test_user_id_real_value_preserved(self) -> None:
        entry = make_entry(user_id="alice")
        assert entry.user_id == "alice"
