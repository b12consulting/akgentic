"""Tests for prompt template and rendering."""

import pytest

from akgentic.llm.prompts import PromptTemplate


class TestPromptTemplate:
    """Tests for PromptTemplate model."""

    def test_instantiation_with_template_and_params(self):
        """Test creating PromptTemplate with template and params."""
        # Subtask 3.2
        tpl = PromptTemplate(
            template="You are {role}.\n{instructions}",
            params={"role": "Architect", "instructions": "Design systems."},
        )
        assert tpl.template == "You are {role}.\n{instructions}"
        assert tpl.params == {"role": "Architect", "instructions": "Design systems."}

    def test_instantiation_with_default_empty_params(self):
        """Test PromptTemplate defaults to empty params dict."""
        # Subtask 3.3
        tpl = PromptTemplate(template="Simple template with no placeholders")
        assert tpl.template == "Simple template with no placeholders"
        assert tpl.params == {}

    def test_pydantic_serialization_model_dump(self):
        """Test Pydantic model_dump serialization."""
        # Subtask 3.8
        tpl = PromptTemplate(
            template="Hello {name}",
            params={"name": "World"},
        )
        data = tpl.model_dump()
        assert data == {
            "template": "Hello {name}",
            "params": {"name": "World"},
        }

    def test_pydantic_serialization_model_dump_json(self):
        """Test Pydantic model_dump_json serialization."""
        # Subtask 3.8
        tpl = PromptTemplate(
            template="Hello {name}",
            params={"name": "World"},
        )
        json_str = tpl.model_dump_json()
        assert '"template":"Hello {name}"' in json_str
        assert '"params":{"name":"World"}' in json_str

    def test_round_trip_serialization(self):
        """Test round-trip: model_dump → PromptTemplate(**data)."""
        # Subtask 3.9
        original = PromptTemplate(
            template="Role: {role}",
            params={"role": "Engineer"},
        )
        data = original.model_dump()
        restored = PromptTemplate(**data)
        assert restored.template == original.template
        assert restored.params == original.params
