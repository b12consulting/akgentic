"""Tests for actor identity: team_id and agent_id assignment and propagation."""

import uuid

from akgentic.core.actor_address import ActorAddress
from akgentic.core.actor_system_impl import ActorSystem
from akgentic.core.agent import Akgent
from akgentic.core.agent_config import BaseConfig
from akgentic.core.agent_state import BaseState


class ChildCreatorAgent(Akgent[BaseConfig, BaseState]):
    """Agent that can create child actors for testing propagation."""

    last_child: ActorAddress | None = None  # type: ignore[assignment]

    def create_child(self, config: BaseConfig | None = None) -> ActorAddress:
        """Create a child actor and return its address."""
        self.last_child = self.createActor(ChildCreatorAgent, config=config)
        return self.last_child

    def get_last_child_team_id(self) -> uuid.UUID | None:
        """Return the team_id of the last created child."""
        assert self.last_child is not None
        return self.last_child.team_id

    def get_last_child_agent_id(self) -> uuid.UUID:
        """Return the agent_id of the last created child."""
        assert self.last_child is not None
        return self.last_child.agent_id


class TestActorIdentityFromSystem:
    """Tests for team_id and agent_id when creating actors from ActorSystem."""

    def test_agent_id_auto_generated(self) -> None:
        """Actor created without agent_id gets a UUID assigned."""
        system = ActorSystem()
        addr = system.createActor(
            ChildCreatorAgent, config=BaseConfig(name="a", role="R")
        )
        assert isinstance(addr.agent_id, uuid.UUID)
        system.shutdown()

    def test_team_id_auto_generated(self) -> None:
        """Actor created without team_id gets a UUID assigned."""
        system = ActorSystem()
        addr = system.createActor(
            ChildCreatorAgent, config=BaseConfig(name="a", role="R")
        )
        assert isinstance(addr.team_id, uuid.UUID)
        system.shutdown()

    def test_agent_id_explicit(self) -> None:
        """Actor created with explicit agent_id keeps that value."""
        system = ActorSystem()
        expected = uuid.uuid4()
        addr = system.createActor(
            ChildCreatorAgent,
            agent_id=expected,
            config=BaseConfig(name="a", role="R"),
        )
        assert addr.agent_id == expected
        system.shutdown()

    def test_team_id_explicit(self) -> None:
        """Actor created with explicit team_id keeps that value."""
        system = ActorSystem()
        expected = uuid.uuid4()
        addr = system.createActor(
            ChildCreatorAgent,
            team_id=expected,
            config=BaseConfig(name="a", role="R"),
        )
        assert addr.team_id == expected
        system.shutdown()

    def test_two_actors_get_different_agent_ids(self) -> None:
        """Two actors created without explicit ids get distinct agent_ids."""
        system = ActorSystem()
        addr1 = system.createActor(ChildCreatorAgent, config=BaseConfig(name="a1", role="R"))
        addr2 = system.createActor(ChildCreatorAgent, config=BaseConfig(name="a2", role="R"))
        assert addr1.agent_id != addr2.agent_id
        system.shutdown()

    def test_two_actors_without_team_id_get_different_team_ids(self) -> None:
        """Two root actors created separately get distinct team_ids."""
        system = ActorSystem()
        addr1 = system.createActor(ChildCreatorAgent, config=BaseConfig(name="a1", role="R"))
        addr2 = system.createActor(ChildCreatorAgent, config=BaseConfig(name="a2", role="R"))
        assert addr1.team_id != addr2.team_id
        system.shutdown()


class TestTeamIdPropagation:
    """Tests for team_id propagation from parent to child actors."""

    def test_child_inherits_parent_team_id(self) -> None:
        """Child actor inherits the parent's team_id."""
        system = ActorSystem()
        parent_team = uuid.uuid4()
        parent_addr = system.createActor(
            ChildCreatorAgent,
            team_id=parent_team,
            config=BaseConfig(name="parent", role="P"),
        )
        proxy = system.proxy_ask(parent_addr, ChildCreatorAgent)
        proxy.create_child(config=BaseConfig(name="child", role="C"))
        child_team = proxy.get_last_child_team_id()

        assert child_team == parent_team
        system.shutdown()

    def test_child_gets_own_agent_id(self) -> None:
        """Child actor gets its own unique agent_id, not the parent's."""
        system = ActorSystem()
        parent_addr = system.createActor(
            ChildCreatorAgent, config=BaseConfig(name="parent", role="P")
        )
        proxy = system.proxy_ask(parent_addr, ChildCreatorAgent)
        proxy.create_child(config=BaseConfig(name="child", role="C"))
        child_agent_id = proxy.get_last_child_agent_id()

        assert isinstance(child_agent_id, uuid.UUID)
        assert child_agent_id != parent_addr.agent_id
        system.shutdown()

    def test_auto_team_id_propagates_to_child(self) -> None:
        """When parent gets an auto-generated team_id, child inherits it."""
        system = ActorSystem()
        parent_addr = system.createActor(
            ChildCreatorAgent, config=BaseConfig(name="parent", role="P")
        )
        parent_team = parent_addr.team_id
        assert parent_team is not None

        proxy = system.proxy_ask(parent_addr, ChildCreatorAgent)
        proxy.create_child(config=BaseConfig(name="child", role="C"))
        child_team = proxy.get_last_child_team_id()

        assert child_team == parent_team
        system.shutdown()

    def test_grandchild_inherits_team_id(self) -> None:
        """team_id propagates through multiple levels: parent -> child -> grandchild."""
        system = ActorSystem()
        team = uuid.uuid4()
        parent_addr = system.createActor(
            ChildCreatorAgent,
            team_id=team,
            config=BaseConfig(name="parent", role="P"),
        )
        parent_proxy = system.proxy_ask(parent_addr, ChildCreatorAgent)
        child_addr = parent_proxy.create_child(config=BaseConfig(name="child", role="C"))

        child_proxy = system.proxy_ask(child_addr, ChildCreatorAgent)

        child_proxy.create_child(config=BaseConfig(name="grandchild", role="G"))
        grandchild_team = child_proxy.get_last_child_team_id()

        assert grandchild_team == team
        system.shutdown()
