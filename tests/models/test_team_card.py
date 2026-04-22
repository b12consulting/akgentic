"""Tests for TeamCard and TeamCardMember models."""

from __future__ import annotations

import pytest
from akgentic.core.agent_card import AgentCard
from akgentic.core.messages import UserMessage

from akgentic.team.models import TeamCard, TeamCardMember

from .conftest import make_agent_card, make_team_card


class TestTeamCardMember:
    """Tests for the TeamCardMember model."""

    def test_construction_with_defaults(self) -> None:
        """TeamCardMember with only card uses default headcount=1 and empty members."""
        card = make_agent_card(name="worker", role="Worker")
        member = TeamCardMember(card=card)
        assert member.card.config.name == "worker"
        assert member.headcount == 1
        assert member.members == []

    def test_construction_with_explicit_headcount(self) -> None:
        """TeamCardMember accepts an explicit headcount value."""
        card = make_agent_card(name="pool-agent", role="PoolAgent")
        member = TeamCardMember(card=card, headcount=5)
        assert member.headcount == 5

    def test_nested_members(self) -> None:
        """TeamCardMember can contain nested members (self-referential)."""
        child = TeamCardMember(card=make_agent_card(name="child", role="Child"))
        parent = TeamCardMember(
            card=make_agent_card(name="parent", role="Parent"),
            members=[child],
        )
        assert len(parent.members) == 1
        assert parent.members[0].card.config.name == "child"


class TestTeamCard:
    """Tests for the TeamCard model."""

    def test_agent_cards_returns_flat_index(self) -> None:
        """agent_cards property returns dict keyed by config name."""
        team = make_team_card(
            entry_point_name="lead",
            entry_point_role="Lead",
            member_names=["dev", "qa"],
            member_roles=["Developer", "QA"],
        )
        cards = team.agent_cards
        assert isinstance(cards, dict)
        assert set(cards.keys()) == {"lead", "dev", "qa"}
        assert all(isinstance(v, AgentCard) for v in cards.values())

    def test_agent_cards_includes_nested_members(self) -> None:
        """agent_cards flattens nested member trees."""
        grandchild = TeamCardMember(
            card=make_agent_card(name="gc", role="GrandChild"),
        )
        child = TeamCardMember(
            card=make_agent_card(name="child", role="Child"),
            members=[grandchild],
        )
        entry = TeamCardMember(
            card=make_agent_card(name="root", role="Root"),
        )
        team = TeamCard(
            name="nested-team",
            description="A nested team",
            entry_point=entry,
            members=[child],
        )
        cards = team.agent_cards
        assert set(cards.keys()) == {"root", "child", "gc"}

    def test_supervisors_returns_first_layer_members(self) -> None:
        """supervisors property returns first-layer members only."""
        worker = TeamCardMember(card=make_agent_card(name="worker", role="Worker"))
        supervisor = TeamCardMember(
            card=make_agent_card(name="sup", role="Supervisor"),
            members=[worker],
        )
        entry = TeamCardMember(card=make_agent_card(name="entry", role="Entry"))
        team = TeamCard(
            name="sup-team",
            description="Team with supervisor",
            entry_point=entry,
            members=[supervisor],
        )
        sups = team.supervisors
        sup_names = {s.config.name for s in sups}
        assert "entry" not in sup_names  # entry point is sender, not supervisor
        assert "sup" in sup_names        # first-layer member
        assert len(sups) == 1

    def test_supervisors_empty_when_no_members(self) -> None:
        """supervisors is empty when members list is empty."""
        team = TeamCard(
            name="solo-team",
            description="Just the entry point",
            entry_point=TeamCardMember(
                card=make_agent_card(name="solo", role="Solo"),
            ),
            members=[],
        )
        assert len(team.supervisors) == 0

    def test_empty_members_list(self) -> None:
        """TeamCard with only entry_point and no members works correctly."""
        team = TeamCard(
            name="solo-team",
            description="Just the entry point",
            entry_point=TeamCardMember(
                card=make_agent_card(name="solo", role="Solo"),
            ),
            members=[],
        )
        assert set(team.agent_cards.keys()) == {"solo"}
        # Entry point without subordinates is not a supervisor
        assert len(team.supervisors) == 0

    def test_message_types_default_empty(self) -> None:
        """message_types defaults to an empty list."""
        team = make_team_card()
        assert team.message_types == []

    def test_agent_cards_raises_on_duplicate_config_name(self) -> None:
        """agent_cards raises ValueError when two members share config.name."""
        entry = TeamCardMember(card=make_agent_card(name="entry", role="Entry"))
        dupe1 = TeamCardMember(card=make_agent_card(name="dupe", role="Role1"))
        dupe2 = TeamCardMember(card=make_agent_card(name="dupe", role="Role2"))
        team = TeamCard(
            name="dupe-team",
            description="Duplicate names",
            entry_point=entry,
            members=[dupe1, dupe2],
        )
        with pytest.raises(ValueError, match="Duplicate config name 'dupe'"):
            team.agent_cards

    def test_supervisors_excludes_entry_point_without_subordinates(self) -> None:
        """Entry point with no members is NOT in supervisors (use entry_proxy instead)."""
        entry = TeamCardMember(card=make_agent_card(name="proxy", role="Proxy"))
        worker = TeamCardMember(card=make_agent_card(name="worker", role="Worker"))
        team = TeamCard(
            name="flat-team",
            description="Flat team with leaf entry point",
            entry_point=entry,
            members=[worker],
        )
        sups = team.supervisors
        sup_names = [s.config.name for s in sups]
        assert "proxy" not in sup_names

    def test_supervisors_excludes_entry_point_even_with_subordinates(self) -> None:
        """Entry point is ALWAYS excluded from supervisors -- it is the sender."""
        child = TeamCardMember(card=make_agent_card(name="child", role="Child"))
        entry = TeamCardMember(
            card=make_agent_card(name="boss", role="Boss"),
            members=[child],
        )
        team = TeamCard(
            name="entry-sup-team",
            description="Entry point supervises",
            entry_point=entry,
            members=[],
        )
        sups = team.supervisors
        assert len(sups) == 0
        sup_names = {s.config.name for s in sups}
        assert "boss" not in sup_names

    def test_supervisors_returns_all_first_layer_members_regardless_of_subordinates(
        self,
    ) -> None:
        """ALL first-layer members are supervisors, regardless of whether they have children."""
        worker = TeamCardMember(card=make_agent_card(name="worker", role="Worker"))
        mid_sup = TeamCardMember(
            card=make_agent_card(name="mid", role="MiddleSup"),
            members=[worker],
        )
        leaf = TeamCardMember(card=make_agent_card(name="leaf", role="Leaf"))
        entry = TeamCardMember(card=make_agent_card(name="lead", role="Lead"))
        team = TeamCard(
            name="mixed-team",
            description="Entry point + member supervisor + leaf member",
            entry_point=entry,
            members=[mid_sup, leaf],
        )
        sups = team.supervisors
        sup_names = {s.config.name for s in sups}
        assert "lead" not in sup_names  # entry point always excluded
        assert "mid" in sup_names       # first-layer member with children
        assert "leaf" in sup_names      # first-layer member without children
        assert len(sups) == 2

    def test_supervisors_excludes_deep_hierarchy(self) -> None:
        """Only first-layer members are supervisors, not deeper nested ones."""
        assistant = TeamCardMember(card=make_agent_card(name="asst", role="Assistant"))
        manager = TeamCardMember(
            card=make_agent_card(name="manager", role="Manager"),
            members=[assistant],
        )
        director = TeamCardMember(
            card=make_agent_card(name="director", role="Director"),
            members=[manager],
        )
        entry = TeamCardMember(card=make_agent_card(name="proxy", role="Proxy"))
        team = TeamCard(
            name="deep-team",
            description="Deep hierarchy",
            entry_point=entry,
            members=[director],
        )
        sups = team.supervisors
        sup_names = {s.config.name for s in sups}
        assert sup_names == {"director"}
        assert "manager" not in sup_names
        assert "asst" not in sup_names

    def test_supervisors_multi_member(self) -> None:
        """Multiple first-layer members are all supervisors."""
        entry = TeamCardMember(card=make_agent_card(name="proxy", role="Proxy"))
        m1 = TeamCardMember(card=make_agent_card(name="m1", role="M1"))
        m2 = TeamCardMember(card=make_agent_card(name="m2", role="M2"))
        m3 = TeamCardMember(card=make_agent_card(name="m3", role="M3"))
        team = TeamCard(
            name="multi-team",
            description="Multi supervisor team",
            entry_point=entry,
            members=[m1, m2, m3],
        )
        sups = team.supervisors
        sup_names = {s.config.name for s in sups}
        assert sup_names == {"m1", "m2", "m3"}
        assert len(sups) == 3


class TestTeamCardSerialization:
    """Tests for serialization round-trip of TeamCard and TeamCardMember."""

    def test_team_card_member_round_trip(self) -> None:
        """TeamCardMember survives model_dump/model_validate round-trip."""
        card = make_agent_card(name="rt-agent", role="RoundTrip")
        member = TeamCardMember(card=card, headcount=3)
        dumped = member.model_dump()
        restored = TeamCardMember.model_validate(dumped)
        assert restored.card.config.name == "rt-agent"
        assert restored.headcount == 3
        assert restored.members == []

    def test_team_card_round_trip(self) -> None:
        """TeamCard survives model_dump/model_validate round-trip."""
        team = make_team_card(
            name="rt-team",
            member_names=["x", "y"],
            member_roles=["X", "Y"],
        )
        dumped = team.model_dump()
        restored = TeamCard.model_validate(dumped)
        assert restored.name == "rt-team"
        assert len(restored.agent_cards) == len(team.agent_cards)
        assert set(restored.agent_cards.keys()) == set(team.agent_cards.keys())

    def test_nested_tree_round_trip(self) -> None:
        """3+ level deep nested tree preserves data through serialization."""
        l3 = TeamCardMember(card=make_agent_card(name="l3", role="L3"))
        l2 = TeamCardMember(
            card=make_agent_card(name="l2", role="L2"),
            members=[l3],
        )
        l1 = TeamCardMember(
            card=make_agent_card(name="l1", role="L1"),
            members=[l2],
        )
        entry = TeamCardMember(card=make_agent_card(name="root", role="Root"))
        team = TeamCard(
            name="deep-team",
            description="Deep nesting",
            entry_point=entry,
            members=[l1],
        )
        dumped = team.model_dump()
        restored = TeamCard.model_validate(dumped)
        assert set(restored.agent_cards.keys()) == {"root", "l1", "l2", "l3"}
        # Verify supervisors preserved (first-layer members only)
        sup_names = {s.config.name for s in restored.supervisors}
        assert sup_names == {"l1"}

    def test_routes_to_preserved_through_serialization(self) -> None:
        """routes_to on AgentCards within the tree survive round-trip."""
        card_with_routes = make_agent_card(
            name="router",
            role="Router",
            routes_to=["TargetA", "TargetB"],
        )
        member = TeamCardMember(card=card_with_routes)
        entry = TeamCardMember(card=make_agent_card(name="entry", role="Entry"))
        team = TeamCard(
            name="routing-team",
            description="Team with routing",
            entry_point=entry,
            members=[member],
        )
        dumped = team.model_dump()
        restored = TeamCard.model_validate(dumped)
        router_card = restored.agent_cards["router"]
        assert router_card.routes_to == ["TargetA", "TargetB"]

    def test_single_member_team_round_trip(self) -> None:
        """Entry-point only team serializes and deserializes correctly."""
        team = TeamCard(
            name="singleton",
            description="Single agent team",
            entry_point=TeamCardMember(
                card=make_agent_card(name="only", role="Only"),
            ),
            members=[],
        )
        dumped = team.model_dump()
        restored = TeamCard.model_validate(dumped)
        assert restored.name == "singleton"
        assert set(restored.agent_cards.keys()) == {"only"}

    def test_message_types_round_trip(self) -> None:
        """message_types with actual type references survive round-trip."""
        team = TeamCard(
            name="typed-team",
            description="Team with message types",
            entry_point=TeamCardMember(
                card=make_agent_card(name="entry", role="Entry"),
            ),
            members=[],
            message_types=[UserMessage],
        )
        dumped = team.model_dump()
        restored = TeamCard.model_validate(dumped)
        assert len(restored.message_types) == 1
        assert restored.message_types[0] is UserMessage
