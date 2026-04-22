"""Tests for TemplateEntry model."""

import pytest
from pydantic import ValidationError

from akgentic.catalog.models.template import TemplateEntry


class TestTemplateEntryValid:
    """Tests for valid TemplateEntry creation."""

    def test_valid_id_and_template(self) -> None:
        entry = TemplateEntry(id="coordinator-v1", template="You are {role} for {team}.")
        assert entry.id == "coordinator-v1"
        assert entry.template == "You are {role} for {team}."

    def test_placeholders_sorted_unique(self) -> None:
        entry = TemplateEntry(
            id="test",
            template="You are {role} for the {team} team. {instructions}",
        )
        assert entry.placeholders == ["instructions", "role", "team"]

    def test_placeholders_no_placeholders(self) -> None:
        entry = TemplateEntry(id="static", template="Hello world")
        assert entry.placeholders == []

    def test_placeholders_duplicate_names(self) -> None:
        entry = TemplateEntry(
            id="dup",
            template="{role} does {task} and {role} does {task} again",
        )
        assert entry.placeholders == ["role", "task"]

    def test_placeholders_single(self) -> None:
        entry = TemplateEntry(id="single", template="Hello {name}!")
        assert entry.placeholders == ["name"]

    def test_placeholders_in_model_dump(self) -> None:
        entry = TemplateEntry(id="test", template="{a} and {b}")
        dump = entry.model_dump()
        assert dump["placeholders"] == ["a", "b"]


class TestTemplateEntryInvalid:
    """Tests for TemplateEntry validation errors."""

    def test_empty_id_raises(self) -> None:
        with pytest.raises(ValidationError):
            TemplateEntry(id="", template="some template")

    def test_empty_template_raises(self) -> None:
        with pytest.raises(ValidationError):
            TemplateEntry(id="valid-id", template="")
