"""Tests for _id ↔ id round-trip mapping helpers (AC-3).

Verifies that to_document() and from_document() correctly map between
MongoDB's _id convention and the model's id field for all four entry types.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from akgentic.catalog.repositories.mongo._helpers import from_document, to_document

if TYPE_CHECKING:
    import pymongo.collection

# Import factory functions from root conftest
from tests.conftest import make_agent, make_team, make_template, make_tool


class TestToDocument:
    """AC-3: to_document() converts model id to MongoDB _id."""

    def test_template_entry_id_becomes_underscore_id(self) -> None:
        """TemplateEntry.id is stored as _id in the document."""
        entry = make_template(id="tpl-1")
        doc = to_document(entry)
        assert "_id" in doc
        assert doc["_id"] == "tpl-1"
        assert "id" not in doc

    def test_tool_entry_id_becomes_underscore_id(self) -> None:
        """ToolEntry.id is stored as _id in the document."""
        entry = make_tool(id="tool-1")
        doc = to_document(entry)
        assert doc["_id"] == "tool-1"
        assert "id" not in doc

    def test_agent_entry_id_becomes_underscore_id(self) -> None:
        """AgentEntry.id is stored as _id in the document."""
        entry = make_agent(id="agent-1")
        doc = to_document(entry)
        assert doc["_id"] == "agent-1"
        assert "id" not in doc

    def test_team_entry_id_becomes_underscore_id(self) -> None:
        """TeamEntry.id is stored as _id in the document."""
        entry = make_team(id="team-1")
        doc = to_document(entry)
        assert doc["_id"] == "team-1"
        assert "id" not in doc


class TestFromDocument:
    """AC-3: from_document() converts MongoDB _id back to model id."""

    def test_template_entry_round_trip(self) -> None:
        """TemplateEntry survives to_document → from_document round-trip."""
        from akgentic.catalog.models.template import TemplateEntry

        original = make_template(id="tpl-rt", template="Hello {name}")
        doc = to_document(original)
        restored = from_document(doc, TemplateEntry)
        assert restored.id == original.id
        assert restored.template == original.template

    def test_tool_entry_round_trip(self) -> None:
        """ToolEntry survives to_document → from_document round-trip."""
        from akgentic.catalog.models.tool import ToolEntry

        original = make_tool(id="tool-rt")
        doc = to_document(original)
        restored = from_document(doc, ToolEntry)
        assert restored.id == original.id
        assert restored.tool_class == original.tool_class

    def test_agent_entry_round_trip(self) -> None:
        """AgentEntry survives to_document → from_document round-trip."""
        from akgentic.catalog.models.agent import AgentEntry

        original = make_agent(id="agent-rt")
        doc = to_document(original)
        restored = from_document(doc, AgentEntry)
        assert restored.id == original.id
        assert restored.card == original.card

    def test_team_entry_round_trip(self) -> None:
        """TeamEntry survives to_document → from_document round-trip."""
        from akgentic.catalog.models.team import TeamEntry

        original = make_team(id="team-rt", name="Round Trip Team")
        doc = to_document(original)
        restored = from_document(doc, TeamEntry)
        assert restored.id == original.id
        assert restored.name == original.name
        assert len(restored.members) == len(original.members)


class TestEdgeCases:
    """AC-3: Edge cases for _id / id mapping."""

    def test_from_document_with_no_underscore_id(self) -> None:
        """Document without _id but with id is handled gracefully."""
        from akgentic.catalog.models.template import TemplateEntry

        doc = {"id": "direct-id", "template": "Hello {world}"}
        entry = from_document(doc, TemplateEntry)
        assert entry.id == "direct-id"

    def test_from_document_with_both_id_and_underscore_id(self) -> None:
        """When both _id and id exist, id takes precedence (no overwrite)."""
        from akgentic.catalog.models.template import TemplateEntry

        doc = {"_id": "from-mongo", "id": "from-model", "template": "Hello {world}"}
        entry = from_document(doc, TemplateEntry)
        assert entry.id == "from-model"

    def test_to_document_preserves_other_fields(self) -> None:
        """to_document() keeps all non-id fields intact."""
        entry = make_template(id="tpl-fields", template="You are {role}")
        doc = to_document(entry)
        assert doc["template"] == "You are {role}"
        assert doc["_id"] == "tpl-fields"

    def test_round_trip_via_mongomock(
        self,
        template_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        """Full round-trip through mongomock insert and find."""
        from akgentic.catalog.models.template import TemplateEntry

        original = make_template(id="mongomock-rt", template="Test {placeholder}")
        doc = to_document(original)
        template_collection.insert_one(doc)

        found = template_collection.find_one({"_id": "mongomock-rt"})
        assert found is not None
        restored = from_document(found, TemplateEntry)
        assert restored.id == original.id
        assert restored.template == original.template
