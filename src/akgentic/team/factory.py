"""TeamFactory: build running teams from TeamCard + ActorSystem."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from akgentic.core.actor_address import ActorAddress
from akgentic.core.actor_system_impl import ActorSystem
from akgentic.core.agent import Akgent
from akgentic.core.agent_config import BaseConfig
from akgentic.core.orchestrator import EventSubscriber, Orchestrator
from akgentic.team.models import TeamCard, TeamCardMember, TeamRuntime

logger = logging.getLogger(__name__)


class TeamFactory:
    """Build running teams from TeamCard + ActorSystem.

    Static builder: TeamFactory.build(team_card, actor_system, subscribers) -> TeamRuntime.
    Creates Orchestrator, spawns agents, wires routes_to, registers subscribers.
    Rollback on partial failure (tear down spawned actors if build fails).
    """

    @staticmethod
    def build(
        team_card: TeamCard,
        actor_system: ActorSystem,
        subscribers: list[EventSubscriber] | None = None,
        team_id: uuid.UUID | None = None,
    ) -> TeamRuntime:
        """Build a running team from a declarative TeamCard.

        Creates an Orchestrator actor, spawns all agents defined in the TeamCard
        member tree, registers subscribers and agent profiles, and returns a
        TeamRuntime handle to the running team.

        Args:
            team_card: Declarative team definition with entry point and members.
            actor_system: The actor system to host the team's actors.
            subscribers: Optional event subscribers to register with the orchestrator.
            team_id: Optional pre-generated team identifier. If None, a new UUID
                is generated. Allows callers (e.g. TeamManager) to know the team_id
                before build completes.

        Returns:
            A TeamRuntime with all actor addresses populated and proxies rebuilt.

        Raises:
            Exception: If any agent spawn fails, all already-spawned actors are
                torn down and the original exception is re-raised.
        """
        team_id = team_id or uuid.uuid4()
        spawned_addrs: list[ActorAddress] = []

        if team_card.entry_point.headcount != 1:
            msg = (
                f"Entry point '{team_card.entry_point.card.config.name}' "
                f"must have headcount=1, got {team_card.entry_point.headcount}"
            )
            raise ValueError(msg)

        try:
            # 1. Create Orchestrator
            orchestrator_addr = actor_system.createActor(
                Orchestrator,
                config=BaseConfig(name="@Orchestrator", role="Orchestrator"),
                team_id=team_id,
            )
            spawned_addrs.append(orchestrator_addr)

            # 2. Get orchestrator proxy and register subscribers
            orchestrator_proxy: Orchestrator = actor_system.proxy_ask(
                orchestrator_addr, Orchestrator
            )
            TeamFactory._register_subscribers(orchestrator_proxy, subscribers)

            # 3. Walk TeamCard tree and spawn all agents
            addrs: dict[str, ActorAddress] = {}
            entry_addr: ActorAddress | None = None

            # Spawn entry point through orchestrator
            entry_addrs = TeamFactory._spawn_member(
                team_card.entry_point,
                orchestrator_addr,
                actor_system,
                spawned_addrs,
            )
            addrs.update(entry_addrs)
            # Entry point always has headcount=1, so use the card name
            entry_addr = entry_addrs[team_card.entry_point.card.config.name]

            # Spawn top-level members through orchestrator
            for member in team_card.members:
                member_addrs = TeamFactory._spawn_member(
                    member,
                    orchestrator_addr,
                    actor_system,
                    spawned_addrs,
                )
                addrs.update(member_addrs)

            # 4. Register hireable agent profiles with orchestrator
            # Only profiles listed in agent_profiles are available for runtime
            # hiring. Instantiated members are already live — registering them
            # would cause the LLM to hire duplicates via role names.
            if team_card.agent_profiles:
                orchestrator_proxy.register_agent_profiles(team_card.agent_profiles)

            # 5. Build supervisor_addrs
            supervisor_addrs: dict[str, ActorAddress] = {}
            for card in team_card.supervisors:
                name = card.config.name
                if name in addrs:
                    supervisor_addrs[name] = addrs[name]

            # 6. Build and return TeamRuntime
            return TeamRuntime(
                id=team_id,
                team=team_card,
                actor_system=actor_system,
                orchestrator_addr=orchestrator_addr,
                entry_addr=entry_addr,
                supervisor_addrs=supervisor_addrs,
                addrs=addrs,
            )

        except Exception:
            # Rollback: stop all already-spawned actors via proxy API
            for addr in reversed(spawned_addrs):
                try:
                    actor_system.proxy_ask(addr, Akgent).stop()
                except Exception:
                    logger.warning("Failed to stop actor during rollback: %s", addr)
            raise

    @staticmethod
    def _register_subscribers(
        orchestrator_proxy: Orchestrator,
        subscribers: list[EventSubscriber] | None,
    ) -> None:
        """Register subscribers and replay missed orchestrator startup events.

        The orchestrator generates its own StartMessage during ``on_start()``,
        before any subscribers are registered. This method replays those
        startup events so subscribers capture the full event history.

        Args:
            orchestrator_proxy: Proxy to the orchestrator actor.
            subscribers: Optional list of event subscribers to register.
        """
        for sub in subscribers or []:
            orchestrator_proxy.subscribe(sub)

        if subscribers:
            for msg in orchestrator_proxy.get_messages():
                for sub in subscribers:
                    sub.on_message(msg)

    @staticmethod
    def _spawn_member(
        member: TeamCardMember,
        parent_addr: ActorAddress,
        actor_system: ActorSystem,
        spawned_addrs: list[ActorAddress],
    ) -> dict[str, ActorAddress]:
        """Spawn a member and its subordinates recursively via public proxy API.

        Uses ``actor_system.proxy_ask(parent_addr, Akgent).createActor()``
        to spawn children through the parent, ensuring context propagation
        (orchestrator, parent, user_id, team_id) is handled by ``createActor()``.

        Args:
            member: The TeamCardMember to spawn.
            parent_addr: Address of the parent actor to spawn through.
            actor_system: The actor system for creating proxies.
            spawned_addrs: Accumulator for rollback tracking.

        Returns:
            Dictionary mapping agent names to their spawned ActorAddresses.
        """
        result: dict[str, ActorAddress] = {}
        agent_class: type[Akgent[Any, Any]] = member.card.get_agent_class()
        name = member.card.config.name
        parent_proxy: Akgent[Any, Any] = actor_system.proxy_ask(parent_addr, Akgent)

        if member.headcount == 1:
            addr = parent_proxy.createActor(
                agent_class,
                config=member.card.get_config_copy(),
            )
            if addr is None:
                msg = f"Failed to spawn agent '{name}'"
                raise RuntimeError(msg)
            spawned_addrs.append(addr)
            result[name] = addr
        else:
            for i in range(member.headcount):
                indexed_name = f"{name}_{i}"
                config = member.card.get_config_copy()
                config.name = indexed_name
                addr = parent_proxy.createActor(
                    agent_class,
                    config=config,
                )
                if addr is None:
                    msg = f"Failed to spawn agent '{indexed_name}'"
                    raise RuntimeError(msg)
                spawned_addrs.append(addr)
                result[indexed_name] = addr

        # Recurse into subordinates using the spawned agent as parent
        if member.members:
            # For headcount == 1, use the single spawned agent as parent
            # For headcount > 1, use the last spawned instance as parent
            last_addr = next(reversed(result.values()))
            for child in member.members:
                child_addrs = TeamFactory._spawn_member(
                    child,
                    last_addr,
                    actor_system,
                    spawned_addrs,
                )
                result.update(child_addrs)

        return result
