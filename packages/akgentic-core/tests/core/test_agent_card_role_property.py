"""Tests for Story 9.2: ``AgentCard.role`` is a derived property of ``config.role``.

Covers acceptance criteria #1, #2, #5, #6, #8, #9, #10 from
``_bmad-output/akgentic-core/stories/9-2-agentcard-role-derives-from-config-role.md``.

Back-compat ACs #3, #4, #7 were dropped per maintainer decision 2026-04-23 —
callers adapt directly to ``config.role``; the legacy top-level ``role=`` hoist
and its ``DeprecationWarning`` were removed from ``AgentCard``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from akgentic.core import ActorSystem, AgentCard, BaseConfig, Orchestrator


class TestRoleIsDerivedProperty:
    """AC #1, #2: role is no longer a field, but ``card.role`` still works."""

    def test_role_not_in_model_fields(self) -> None:
        """AC #1: ``role`` does not appear in ``AgentCard.model_fields``."""
        assert "role" not in AgentCard.model_fields

    def test_role_property_returns_config_role(self) -> None:
        """AC #2: ``card.role`` returns ``card.config.role``."""
        card = AgentCard(
            description="Manager",
            skills=[],
            agent_class="akgentic.core.Akgent",
            config=BaseConfig(name="mgr", role="Manager"),
        )
        assert card.role == "Manager"
        assert card.role == card.config.role


class TestYamlPayloadValidates:
    """AC #6: payload without top-level ``role:`` validates cleanly."""

    def test_yaml_payload_without_top_level_role_validates(self) -> None:
        payload = {
            "description": "Manager",
            "skills": [],
            "agent_class": "akgentic.core.Akgent",
            "config": {"name": "mgr", "role": "Manager"},
        }
        card = AgentCard.model_validate(payload)
        assert card.role == "Manager"


class TestEmptyConfigRoleIsError:
    """AC #5: empty ``config.role`` is a validation error."""

    def test_empty_config_role_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            AgentCard(
                description="Nameless",
                skills=[],
                agent_class="akgentic.core.Akgent",
                config=BaseConfig(role=""),
            )
        assert "config.role" in str(exc_info.value)


class TestModelDumpOmitsTopLevelRole:
    """AC #9: ``model_dump()`` does not emit a top-level ``role`` key."""

    def test_model_dump_no_top_level_role(self) -> None:
        card = AgentCard(
            description="Manager",
            skills=[],
            agent_class="akgentic.core.Akgent",
            config=BaseConfig(name="mgr", role="Manager"),
        )
        dumped = card.model_dump()
        assert "role" not in dumped
        # Role still reachable via config
        assert dumped["config"]["role"] == "Manager"


class TestRoundTripStability:
    """AC #10: dump → validate round-trips are idempotent."""

    def _base_card(self) -> AgentCard:
        return AgentCard(
            description="Manager",
            skills=["coordination"],
            agent_class="akgentic.core.Akgent",
            config=BaseConfig(name="mgr", role="Manager"),
            routes_to=["Worker"],
        )

    def test_round_trip_preserves_card(self) -> None:
        original = self._base_card()
        round1 = AgentCard.model_validate(original.model_dump())
        round2 = AgentCard.model_validate(round1.model_dump())
        assert round1.role == original.role == "Manager"
        assert round2.role == original.role
        assert round1.config.role == original.config.role
        assert round1.skills == original.skills
        assert round2.model_dump() == round1.model_dump()


class TestOrchestratorRegistryKey:
    """AC #8: Orchestrator registers by ``card.config.role`` (via the property)."""

    def test_registry_key_is_config_role(self) -> None:
        system = ActorSystem()
        try:
            orchestrator_addr = system.createActor(
                Orchestrator,
                config=BaseConfig(name="orchestrator", role="Orchestrator"),
            )
            orch_proxy = system.proxy_ask(orchestrator_addr, Orchestrator)

            card_a = AgentCard(
                description="A",
                skills=[],
                agent_class="akgentic.core.Akgent",
                config=BaseConfig(name="a", role="AgentA"),
            )
            card_b = AgentCard(
                description="B",
                skills=[],
                agent_class="akgentic.core.Akgent",
                config=BaseConfig(name="b", role="AgentB"),
            )
            orch_proxy.register_agent_profile(card_a)
            orch_proxy.register_agent_profile(card_b)

            # Lookup by the config.role string returns the right card.
            got_a = orch_proxy.get_agent_profile("AgentA")
            got_b = orch_proxy.get_agent_profile("AgentB")
            assert got_a is not None and got_a.config.role == "AgentA"
            assert got_b is not None and got_b.config.role == "AgentB"
        finally:
            system.shutdown()


# AC #11 (agent YAML fixtures drop top-level `role:`) is intentionally NOT
# asserted in this test module. Those fixtures live in the PARENT repo
# (``data/catalog/agent-team-v1/agent/*.yaml``) — outside the ``akgentic-core``
# submodule boundary. A submodule test that reads parent-repo files would
# silently ``pytest.skip`` in standalone CI (where the submodule is checked
# out on its own) and would violate CLAUDE.md Golden Rule #4.
