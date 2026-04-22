"""Example 06: Agent Profile Discovery via AgentCards

Demonstrates how agents can discover team capabilities through an
AgentCard catalog maintained by the Orchestrator.

Concepts:
- AgentCard: Profile definitions for agent types
- Catalog registration: Adding profiles to the orchestrator
- Discovery: Agents finding profiles by role or skill
- Config: Default config stored in cards
"""

import time

from akgentic.core import (
    ActorSystem,
    AgentCard,
    Akgent,
    BaseConfig,
    Orchestrator,
)

# =============================================================================
# STEP 1: Define Agent Types
# =============================================================================


class ResearchAgent(Akgent):
    """Agent that performs research tasks."""

    def on_start(self) -> None:
        """Initialize the research agent."""
        print(f"[{self.config.name}] Research agent initialized")


class WriterAgent(Akgent):
    """Agent that writes content."""

    def on_start(self) -> None:
        """Initialize the writer agent."""
        print(f"[{self.config.name}] Writer agent initialized")


class CoordinatorAgent(Akgent):
    """Agent that discovers and coordinates other agents."""

    def on_start(self) -> None:
        """Initialize the coordinator agent."""
        print(f"[{self.config.name}] Coordinator initialized")

    def discover_team_capabilities(self) -> None:
        """Discover what agent profiles are available in the team."""
        print(f"\n[{self.config.name}] Discovering team capabilities...")

        # Get full catalog
        catalog = self.discover_catalog()
        print(f"[{self.config.name}] Found {len(catalog)} agent profiles:")
        for card in catalog:
            print(f"  - {card.role}: {card.description}")
            print(f"    Skills: {', '.join(card.skills)}")
            if card.routes_to:
                print(f"    Routes to: {', '.join(card.routes_to)}")
            else:
                print("    Routes to: (any role)")

        # Find specific profile
        print(f"\n[{self.config.name}] Looking for ResearchAgent profile...")
        research_profile = self.get_agent_card("ResearchAgent")
        if research_profile:
            print(f"[{self.config.name}] Found ResearchAgent:")
            print(f"  Description: {research_profile.description}")
            print(f"  Skills: {research_profile.skills}")
            config = research_profile.get_config_copy()
            print(f"  Default config: name={config.name}, role={config.role}")

            # Check routing permissions
            print(f"  Can route to WriterAgent: {research_profile.can_route_to('WriterAgent')}")
            print(f"  Can route to AnalystAgent: {research_profile.can_route_to('AnalystAgent')}")
            print(f"  Can route to UnknownAgent: {research_profile.can_route_to('UnknownAgent')}")

        # Find agents with specific skill
        print(f"\n[{self.config.name}] Finding agents with 'writing' skill...")
        writers = self.find_agents_with_skill("writing")
        print(f"[{self.config.name}] Found {len(writers)} agent(s) with writing skill:")
        for card in writers:
            print(f"  - {card.role}")


# =============================================================================
# STEP 2: Main Execution
# =============================================================================


def main() -> None:
    """Run the agent card discovery example."""
    print("[Agent Cards] Starting agent profile discovery demo...\n")

    actor_system = ActorSystem()

    try:
        # Create Orchestrator
        orchestrator_addr = actor_system.createActor(
            Orchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
        )

        orch_proxy = actor_system.proxy_ask(orchestrator_addr, Orchestrator)

        # Register agent profiles in the catalog
        print("[Setup] Registering agent profiles in catalog...\n")

        orch_proxy.register_agent_profile(
            AgentCard(
                role="ResearchAgent",
                description="Performs web research and data gathering",
                skills=["web_search", "pdf_extraction", "arxiv_lookup"],
                agent_class="examples.agent_cards.ResearchAgent",
                config=BaseConfig(name="researcher", role="ResearchAgent"),
                routes_to=["WriterAgent", "AnalystAgent"],  # Can only route to these
                metadata={"version": "1.0", "max_concurrent_tasks": 5},
            )
        )

        orch_proxy.register_agent_profile(
            AgentCard(
                role="WriterAgent",
                description="Writes content based on research findings",
                skills=["writing", "summarization", "formatting"],
                agent_class="examples.agent_cards.WriterAgent",
                config=BaseConfig(name="writer", role="WriterAgent"),
                routes_to=["AnalystAgent"],  # Can only route to analyst
                metadata={"version": "1.0", "max_words": 2000},
            )
        )

        orch_proxy.register_agent_profile(
            AgentCard(
                role="AnalystAgent",
                description="Analyzes data and provides insights",
                skills=["data_analysis", "visualization", "web_search"],
                agent_class="examples.agent_cards.AnalystAgent",
                config=BaseConfig(name="analyst", role="AnalystAgent"),
                routes_to=[],  # Empty = no restrictions, can route to anyone
                metadata={"version": "1.0"},
            )
        )

        print("[Setup] Agent profiles registered\n")

        # Query catalog capabilities
        print("[Catalog] Available roles:", orch_proxy.get_available_roles())
        print("[Catalog] Available skills:", orch_proxy.get_available_skills())
        print()

        # Create coordinator agent (via orchestrator to get orchestrator reference)
        coordinator_addr = orch_proxy.createActor(
            CoordinatorAgent,
            config=BaseConfig(name="coordinator", role="CoordinatorAgent"),
        )

        time.sleep(0.2)

        # Coordinator discovers capabilities
        coordinator_proxy = actor_system.proxy_tell(coordinator_addr, CoordinatorAgent)
        coordinator_proxy.discover_team_capabilities()

        time.sleep(0.5)

        print("\n[Agent Cards] Demo complete!")

    finally:
        actor_system.shutdown()


if __name__ == "__main__":
    main()
