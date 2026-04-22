"""Full lifecycle integration test: create → send → stop → restore → send again.

Reproduces the exact src/agent_team flow with a real LLM to prove ADR-005 bugs
exist (duplicate hiring, orchestrator death, restore crash). Requires OPENAI_API_KEY.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pykka
import pytest
from akgentic.agent import AgentConfig, AgentMessage, BaseAgent, HumanProxy
from akgentic.core import AgentCard, EventSubscriber
from akgentic.core.actor_system_impl import ActorSystem
from akgentic.core.agent_config import BaseConfig
from akgentic.core.messages.message import Message
from akgentic.core.messages.orchestrator import SentMessage
from akgentic.llm import ModelConfig, PromptTemplate
from akgentic.tool.planning import PlanningTool, UpdatePlanning
from akgentic.tool.search import SearchTool, WebSearch
from dotenv import load_dotenv

from akgentic.team import TeamCard, TeamCardMember, TeamManager, TeamStatus, YamlEventStore

LLM_MODEL = "gpt-5.2"


# --- EventCollector ---


class EventCollector(EventSubscriber):
    """Collects SentMessage events for test assertions."""

    def __init__(self) -> None:
        self.sent_messages: list[SentMessage] = []

    def on_message(self, message: Message) -> None:
        """Record SentMessage events."""
        if isinstance(message, SentMessage):
            self.sent_messages.append(message)

    def on_stop(self) -> None:
        """No-op on orchestrator stop."""

    def clear(self) -> None:
        """Reset all collected events."""
        self.sent_messages.clear()


# --- wait_for_stable_messages ---


def wait_for_stable_messages(
    collector: EventCollector,
    stable_seconds: float = 15.0,
    timeout: float = 120.0,
) -> None:
    """Poll SentMessage count; stable = unchanged for stable_seconds; fail on timeout."""
    start = time.monotonic()
    last_count = len(collector.sent_messages)
    last_change = start

    while True:
        now = time.monotonic()
        if now - start > timeout:
            pytest.fail(
                f"Conversation did not stabilize within {timeout}s "
                f"(last count: {len(collector.sent_messages)} messages)"
            )
        current_count = len(collector.sent_messages)
        if current_count != last_count:
            last_count = current_count
            last_change = now
        elif now - last_change >= stable_seconds:
            return  # Stable
        time.sleep(2.0)


# --- TeamCard construction ---


def build_llm_team_card() -> TeamCard:
    """Build a TeamCard programmatically matching src/agent_team structure."""
    search_tool = SearchTool(web_search=WebSearch(max_results=3))
    planning_tool = PlanningTool(
        update_planning=UpdatePlanning(instructions="Keep the plan updated.")
    )
    tools = [search_tool, planning_tool]

    human_card = AgentCard(
        role="Human",
        description="Human user interface",
        skills=[],
        agent_class=HumanProxy,
        config=BaseConfig(name="@Human", role="Human"),
        routes_to=["@Manager"],
    )

    manager_card = AgentCard(
        role="Manager",
        description="Helpful manager coordinating team work",
        skills=["coordination", "delegation"],
        agent_class=BaseAgent,
        config=AgentConfig(
            name="@Manager",
            role="Manager",
            prompt=PromptTemplate(
                template="You are a helpful manager. Coordinate the team effectively.",
            ),
            model_cfg=ModelConfig(provider="openai", model=LLM_MODEL, temperature=0.3),
            tools=tools,
        ),
        routes_to=["@Assistant", "@Expert"],
    )

    assistant_card = AgentCard(
        role="Assistant",
        description="Helpful assistant providing support",
        skills=["research", "writing"],
        agent_class=BaseAgent,
        config=AgentConfig(
            name="@Assistant",
            role="Assistant",
            prompt=PromptTemplate(
                template="You are a helpful assistant. Provide clear and accurate information.",
            ),
            model_cfg=ModelConfig(provider="openai", model=LLM_MODEL, temperature=0.3),
            tools=tools,
        ),
    )

    expert_card = AgentCard(
        role="Expert",
        description="Helpful expert providing specialized knowledge",
        skills=["analysis", "problem-solving"],
        agent_class=BaseAgent,
        config=AgentConfig(
            name="@Expert",
            role="Expert",
            prompt=PromptTemplate(
                template="You are a helpful expert. Provide deep specialized knowledge.",
            ),
            model_cfg=ModelConfig(provider="openai", model=LLM_MODEL, temperature=0.3),
            tools=tools,
        ),
    )

    # Build TeamCard — mirrors src/catalog/teams/agent-team.yaml
    assistant_member = TeamCardMember(card=assistant_card)
    expert_member = TeamCardMember(card=expert_card)
    manager_member = TeamCardMember(
        card=manager_card,
        members=[assistant_member, expert_member],
    )
    human_member = TeamCardMember(card=human_card)

    return TeamCard(
        name="agent-team",
        description="Manager coordinating Assistant and Expert agents",
        entry_point=human_member,
        members=[manager_member],
        message_types=[AgentMessage],
    )


# --- Fixture ---


@pytest.fixture()
def llm_team_infrastructure(tmp_path: Path) -> dict[str, Any]:
    """Create infrastructure for LLM integration tests."""
    # Load API key from project root .env
    project_root = Path(__file__).resolve().parents[4]
    load_dotenv(project_root / ".env")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        pytest.fail(
            "OPENAI_API_KEY not set in environment or .env — "
            "cannot run LLM integration tests"
        )

    # Infrastructure
    actor_system = ActorSystem()
    event_store = YamlEventStore(tmp_path / "team-data")
    collector = EventCollector()

    team_manager = TeamManager(
        actor_system=actor_system,
        event_store=event_store,
        subscribers=[collector],
    )

    yield {
        "actor_system": actor_system,
        "team_manager": team_manager,
        "collector": collector,
    }

    pykka.ActorRegistry.stop_all()


# --- Integration Test ---


@pytest.mark.integration
def test_full_lifecycle_create_send_stop_restore(
    llm_team_infrastructure: dict[str, Any],
) -> None:
    """Full lifecycle: create → send → stop → restore → send again."""
    infra = llm_team_infrastructure
    team_manager: TeamManager = infra["team_manager"]
    collector: EventCollector = infra["collector"]
    team_card = build_llm_team_card()

    # --- Phase 1: Create and Send (AC: 1) ---
    runtime = team_manager.create_team(team_card)
    time.sleep(0.3)

    initial_team_size = len(runtime.addrs)

    prompt = (
        "Could you ask your expert for information on the best practices "
        "for tea harvesting in Kenya? Ask your assistant to provide me a "
        "well-formatted report with the expert information."
    )
    runtime.send(prompt)

    wait_for_stable_messages(collector, stable_seconds=15, timeout=120)

    # Assertions — Phase 1
    final_team_size = len(runtime.addrs)
    assert final_team_size == initial_team_size, (
        f"Team grew from {initial_team_size} to {final_team_size} — duplicate hiring"
    )
    # Orchestrator alive check — would throw ActorDeadError if dead
    roster_after = runtime.orchestrator_proxy.get_team()
    assert roster_after is not None

    # --- Phase 2: Stop (AC: 2) ---
    team_id = runtime.id
    team_manager.stop_team(team_id)

    process = team_manager.get_team(team_id)
    assert process is not None
    assert process.status == TeamStatus.STOPPED

    # --- Phase 3: Restore and Continue (AC: 3) ---
    collector.clear()

    restored_runtime = team_manager.resume_team(team_id)
    time.sleep(0.3)

    # Verify restored addresses are live ActorAddressImpl, not stale proxies
    from akgentic.core.actor_address_impl import ActorAddressImpl, ActorAddressProxy

    restored_roster = restored_runtime.orchestrator_proxy.get_team()
    for addr in restored_roster:
        assert isinstance(addr, ActorAddressImpl), (
            f"Expected ActorAddressImpl but got {type(addr).__name__} for '{addr.name}'"
        )
        assert not isinstance(addr, ActorAddressProxy), (
            f"ActorAddressProxy leaked into restored roster for '{addr.name}'"
        )

    # Restored team should have same size as initial
    restored_team_size = len(restored_runtime.addrs)
    assert restored_team_size == initial_team_size, (
        f"Restored team size {restored_team_size} != initial {initial_team_size}"
    )

    # Send a follow-up prompt through the restored team
    follow_up = (
        "Based on the earlier tea harvesting research, what are the top 3 "
        "takeaways? Keep it brief."
    )
    restored_runtime.send(follow_up)

    wait_for_stable_messages(collector, stable_seconds=15, timeout=120)

    # Assertions — Phase 3
    # Orchestrator alive check — would throw ActorDeadError if dead
    post_convo_roster = restored_runtime.orchestrator_proxy.get_team()
    assert post_convo_roster is not None
