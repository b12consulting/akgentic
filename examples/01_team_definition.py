"""Example 01: Team Definition -- TeamCard & TeamCardMember Hierarchies.

Demonstrates how to build a TeamCard with a hierarchical TeamCardMember tree,
inspect the flat agent_cards index and supervisors list, print the tree structure,
and perform a Pydantic round-trip serialization.

Run:
    uv run python packages/akgentic-team/examples/01_team_definition.py
"""

from akgentic.core.agent import Akgent
from akgentic.core.agent_card import AgentCard
from akgentic.core.agent_config import BaseConfig

from akgentic.team.models import TeamCard, TeamCardMember


def main() -> None:
    """Build a team hierarchy, inspect it, and verify round-trip serialization."""

    # --- 1.1: Create AgentCard definitions for 4 agents ---
    manager_card = AgentCard(
        role="Manager",
        description="Coordinates research tasks and reviews deliverables",
        skills=["coordination", "task-assignment", "review"],
        agent_class=Akgent,
        config=BaseConfig(name="manager", role="Manager"),
        routes_to=["researcher", "reviewer"],
    )

    researcher_card = AgentCard(
        role="Researcher",
        description="Performs in-depth research on assigned topics",
        skills=["web-search", "data-gathering", "summarization"],
        agent_class=Akgent,
        config=BaseConfig(name="researcher", role="Researcher"),
    )

    analyst_card = AgentCard(
        role="Analyst",
        description="Analyzes data and produces structured reports",
        skills=["data-analysis", "statistics", "visualization"],
        agent_class=Akgent,
        config=BaseConfig(name="analyst", role="Analyst"),
    )

    reviewer_card = AgentCard(
        role="Reviewer",
        description="Reviews deliverables for quality and accuracy",
        skills=["quality-check", "fact-verification", "feedback"],
        agent_class=Akgent,
        config=BaseConfig(name="reviewer", role="Reviewer"),
    )

    print("=== AgentCards Created ===")
    for card in [manager_card, researcher_card, analyst_card, reviewer_card]:
        print(f"  {card.config.name}: role={card.role}, skills={card.skills}")

    # --- 1.2: Build TeamCardMember hierarchy ---
    # Analyst is subordinate to researcher
    analyst_member = TeamCardMember(card=analyst_card)
    researcher_member = TeamCardMember(card=researcher_card, members=[analyst_member])
    reviewer_member = TeamCardMember(card=reviewer_card)
    manager_member = TeamCardMember(card=manager_card)

    # --- 1.3: Create TeamCard ---
    # entry_point is the manager; researcher (with analyst) and reviewer are members
    team_card = TeamCard(
        name="research-team",
        description="A research team with a manager, researcher, analyst, and reviewer",
        entry_point=manager_member,
        members=[researcher_member, reviewer_member],
        message_types=[],
    )

    print(f"\n=== TeamCard: {team_card.name} ===")
    print(f"  Description: {team_card.description}")
    print(f"  Entry point: {team_card.entry_point.card.config.name}")

    # --- 1.4: Inspect agent_cards -- flat dict of all 4 AgentCards ---
    agent_cards = team_card.agent_cards
    print(f"\n=== agent_cards (flat index, {len(agent_cards)} agents) ===")
    for name, card in agent_cards.items():
        print(f"  {name}: role={card.role}")

    assert len(agent_cards) == 4, f"Expected 4 agent cards, got {len(agent_cards)}"
    assert "manager" in agent_cards, "manager missing from agent_cards"
    assert "researcher" in agent_cards, "researcher missing from agent_cards"
    assert "analyst" in agent_cards, "analyst missing from agent_cards"
    assert "reviewer" in agent_cards, "reviewer missing from agent_cards"

    # --- 1.5: Inspect supervisors ---
    # Supervisors are members that have subordinate members:
    #   - manager has members [researcher_member, reviewer_member] via TeamCard.members
    #     (but manager itself has members=[] on TeamCardMember)
    #   - researcher has members=[analyst_member]
    # So only researcher is a supervisor at the TeamCardMember level.
    supervisors = team_card.supervisors
    supervisor_names = [s.config.name for s in supervisors]
    print(f"\n=== Supervisors ({len(supervisors)}) ===")
    for sup in supervisors:
        print(f"  {sup.config.name}: role={sup.role}")

    assert "researcher" in supervisor_names, "researcher should be a supervisor (has analyst)"
    # manager_member has members=[] (it's the entry_point, not a member with subordinates)
    # The TeamCard.members are top-level, not subordinates of manager_member itself
    print("  (Supervisors are members whose .members list is non-empty)")

    # --- 1.6: Print team tree structure ---
    print("\n=== Team Tree ===")

    def print_tree(member: TeamCardMember, indent: int = 0) -> None:
        """Print a TeamCardMember tree with indentation."""
        prefix = "  " * indent
        role = member.card.role
        name = member.card.config.name
        hc = member.headcount
        subs = len(member.members)
        print(f"{prefix}- {name} (role={role}, headcount={hc}, subordinates={subs})")
        for child in member.members:
            print_tree(child, indent + 1)

    print(f"Team: {team_card.name}")
    print("Entry point:")
    print_tree(team_card.entry_point, indent=1)
    print("Members:")
    for member in team_card.members:
        print_tree(member, indent=1)

    # --- 1.7: Pydantic round-trip serialization ---
    print("\n=== Pydantic Round-Trip ===")
    dumped = team_card.model_dump()
    print(f"  model_dump() keys: {list(dumped.keys())}")
    restored = TeamCard.model_validate(dumped)
    print(f"  model_validate() -> TeamCard(name={restored.name!r})")

    # Verify structural equality
    assert restored.name == team_card.name, "Name mismatch after round-trip"
    assert restored.description == team_card.description, "Description mismatch"
    assert len(restored.agent_cards) == len(team_card.agent_cards), "Agent count mismatch"
    assert set(restored.agent_cards.keys()) == set(
        team_card.agent_cards.keys()
    ), "Agent names mismatch"

    restored_supervisors = [s.config.name for s in restored.supervisors]
    assert set(restored_supervisors) == set(supervisor_names), "Supervisors mismatch"

    # --- 1.8: Assert statements verify exit 0 = success ---
    print("\n=== All assertions passed! ===")
    print("Example 01 complete: TeamCard hierarchy built, inspected, and round-tripped.")


if __name__ == "__main__":
    main()
