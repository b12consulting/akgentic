"""Tests for AgentCard.config coercion to agent_class's declared ConfigType.

These tests exercise the ``@model_validator(mode="before")`` on :class:`AgentCard`
that walks ``agent_class.__orig_bases__`` to find a concrete ``Akgent[X, Y]``
binding and coerces ``config`` accordingly (Story 9.1).

All fixtures are defined **locally** in this module — ``akgentic-core`` must
not import from ``akgentic.agent``, ``akgentic.catalog``, etc., so we cannot
reach for ``akgentic.agent.BaseAgent``/``AgentConfig`` to test the real-world
case. Instead we mirror those classes with typed subclasses here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from akgentic.core import AgentCard, Akgent, BaseConfig
from akgentic.core.agent_card import _CONFIG_TYPE_CACHE, _extract_config_type
from akgentic.core.agent_state import BaseState

# ---------------------------------------------------------------------------
# Fixtures — mirror akgentic.agent.BaseAgent / AgentConfig locally.
# ---------------------------------------------------------------------------


class RichConfig(BaseConfig):
    """Mirrors the shape of ``akgentic.agent.AgentConfig``.

    Adds fields that are *not* on :class:`BaseConfig`, so that a silent
    coercion to ``BaseConfig`` would drop them.
    """

    prompt: str = ""
    model_name: str = ""
    tools: list[str] = []


class RichAgent(Akgent[RichConfig, BaseState]):
    """Parameterised ``Akgent[RichConfig, BaseState]`` — the canonical case."""


class DerivedRichAgent(RichAgent):
    """Indirect inheritance — no new generic args; walker must recurse."""


class CustomConfig(BaseConfig):
    """User-defined config with an extra field."""

    extra: str = ""


class CustomAgent(Akgent[CustomConfig, BaseState]):
    """User-defined agent binding its own ``CustomConfig``."""


class GenericButNotAkgent[T: BaseConfig]:
    """Unrelated generic class — validator must ignore it entirely."""


class UnparameterisedAgent(Akgent):  # type: ignore[type-arg]
    """Akgent subclass with no concrete type arguments — ConfigType unresolved."""


# ---------------------------------------------------------------------------
# Housekeeping — reset the module-level cache between tests.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_config_type_cache() -> None:
    """Clear the memoisation cache so test ordering can't mask bugs."""
    _CONFIG_TYPE_CACHE.clear()


# ---------------------------------------------------------------------------
# Helper tests for _extract_config_type (unit-level).
# ---------------------------------------------------------------------------


class TestExtractConfigType:
    """Unit tests for the private ``_extract_config_type`` walker."""

    def test_direct_generic_binding(self) -> None:
        assert _extract_config_type(RichAgent) is RichConfig

    def test_indirect_inheritance(self) -> None:
        # DerivedRichAgent has no __orig_bases__ entry for Akgent[...]; the
        # walker must climb the MRO and still find RichConfig (AC #5).
        assert _extract_config_type(DerivedRichAgent) is RichConfig

    def test_user_defined_config(self) -> None:
        assert _extract_config_type(CustomAgent) is CustomConfig

    def test_unparameterised_returns_none(self) -> None:
        # AC #7: TypeVar args → treat as unresolved, not a hard error.
        assert _extract_config_type(UnparameterisedAgent) is None

    def test_non_akgent_class_returns_none(self) -> None:
        # AC #7: not an Akgent subclass → None.
        assert _extract_config_type(GenericButNotAkgent) is None
        assert _extract_config_type(BaseConfig) is None

    def test_result_is_cached(self) -> None:
        _extract_config_type(RichAgent)
        assert RichAgent in _CONFIG_TYPE_CACHE
        assert _CONFIG_TYPE_CACHE[RichAgent] is RichConfig


# ---------------------------------------------------------------------------
# Integration tests — AgentCard.model_validate path.
# ---------------------------------------------------------------------------


class TestAgentCardConfigCoercion:
    """End-to-end tests for AgentCard config coercion via model validation."""

    def test_string_agent_class_with_dict_config_coerces(self) -> None:
        """AC #1, #4: string FQCN + dict config → RichConfig instance with all fields."""
        fqcn = f"{RichAgent.__module__}.{RichAgent.__qualname__}"
        card = AgentCard.model_validate(
            {
                "role": "Rich",
                "description": "Rich agent",
                "skills": [],
                "agent_class": fqcn,
                "config": {
                    "name": "@rich",
                    "role": "Rich",
                    "prompt": "Be helpful.",
                    "model_name": "gpt-x",
                    "tools": ["t1", "t2"],
                },
            }
        )
        assert isinstance(card.config, RichConfig)
        assert card.config.prompt == "Be helpful."
        assert card.config.model_name == "gpt-x"
        assert card.config.tools == ["t1", "t2"]

    def test_class_agent_class_with_dict_config_coerces(self) -> None:
        """AC #4: class object + dict config → RichConfig instance."""
        card = AgentCard(
            role="Rich",
            description="Rich agent",
            skills=[],
            agent_class=RichAgent,
            config={"name": "@rich", "role": "Rich", "prompt": "hi", "tools": []},  # type: ignore[arg-type]
        )
        assert isinstance(card.config, RichConfig)
        assert card.config.prompt == "hi"

    def test_human_proxy_no_op_stays_base_config(self) -> None:
        """AC #2: generic resolves to BaseConfig → no coercion attempt, clean validation.

        ``UserProxy(Akgent[BaseConfig, BaseState])`` mirrors the real-world
        ``HumanProxy``. Construction with a plain dict must succeed and produce
        a :class:`BaseConfig` instance without raising.
        """
        card = AgentCard.model_validate(
            {
                "role": "Human",
                "description": "Human in the loop",
                "skills": [],
                "agent_class": "akgentic.core.UserProxy",
                "config": {"name": "@Human", "role": "Human"},
            }
        )
        assert type(card.config) is BaseConfig
        assert card.config.name == "@Human"

    def test_already_typed_config_passthrough(self) -> None:
        """AC #3: passing a concrete RichConfig instance leaves it untouched."""
        cfg = RichConfig(name="@rich", role="Rich", prompt="p", tools=["a"])
        card = AgentCard(
            role="Rich",
            description="Rich agent",
            skills=[],
            agent_class=RichAgent,
            config=cfg,
        )
        assert card.config is cfg or card.config == cfg
        assert isinstance(card.config, RichConfig)
        assert card.config.prompt == "p"

    def test_indirect_inheritance_resolves_to_rich_config(self) -> None:
        """AC #5: subclass with no new generics inherits RichConfig binding."""
        fqcn = f"{DerivedRichAgent.__module__}.{DerivedRichAgent.__qualname__}"
        card = AgentCard.model_validate(
            {
                "role": "Derived",
                "description": "Derived rich agent",
                "skills": [],
                "agent_class": fqcn,
                "config": {"name": "@d", "role": "Derived", "prompt": "hello"},
            }
        )
        assert isinstance(card.config, RichConfig)
        assert card.config.prompt == "hello"

    def test_user_subclass_with_own_config_type(self) -> None:
        """AC #6: CustomAgent bound to CustomConfig → coerces to CustomConfig."""
        fqcn = f"{CustomAgent.__module__}.{CustomAgent.__qualname__}"
        card = AgentCard.model_validate(
            {
                "role": "Custom",
                "description": "Custom agent",
                "skills": [],
                "agent_class": fqcn,
                "config": {"name": "@c", "role": "Custom", "extra": "hello"},
            }
        )
        assert isinstance(card.config, CustomConfig)
        assert card.config.extra == "hello"

    def test_unparameterised_agent_no_op(self) -> None:
        """AC #7: unparameterised Akgent subclass → validator no-op, dict becomes BaseConfig."""
        fqcn = f"{UnparameterisedAgent.__module__}.{UnparameterisedAgent.__qualname__}"
        card = AgentCard.model_validate(
            {
                "role": "Old",
                "description": "Old agent",
                "skills": [],
                "agent_class": fqcn,
                "config": {"name": "@old", "role": "Old"},
            }
        )
        assert type(card.config) is BaseConfig
        assert card.config.name == "@old"

    def test_non_akgent_class_no_op(self) -> None:
        """AC #7: non-Akgent agent_class → validator no-op, dict becomes BaseConfig."""
        fqcn = f"{GenericButNotAkgent.__module__}.{GenericButNotAkgent.__qualname__}"
        card = AgentCard.model_validate(
            {
                "role": "Weird",
                "description": "Non-Akgent class",
                "skills": [],
                "agent_class": fqcn,
                "config": {"name": "@weird", "role": "Weird"},
            }
        )
        assert type(card.config) is BaseConfig
        assert card.config.name == "@weird"

    def test_unresolvable_agent_class_raises_validation_error(self) -> None:
        """AC #8: bad FQCN + dict config surfaces via ValidationError naming the path."""
        with pytest.raises(ValidationError) as exc_info:
            AgentCard.model_validate(
                {
                    "role": "Bad",
                    "description": "Bad FQCN",
                    "skills": [],
                    "agent_class": "not.a.real.module.Thing",
                    "config": {"name": "@bad", "role": "Bad"},
                }
            )
        assert "not.a.real.module.Thing" in str(exc_info.value)

    def test_round_trip_model_dump_and_validate(self) -> None:
        """AC #9: card.model_dump() → AgentCard.model_validate(...) preserves every field."""
        original = AgentCard(
            role="Rich",
            description="Rich agent",
            skills=["s1"],
            agent_class=RichAgent,
            config=RichConfig(
                name="@rich",
                role="Rich",
                prompt="Be helpful.",
                model_name="gpt-x",
                tools=["t1"],
            ),
        )
        dumped = original.model_dump()
        restored = AgentCard.model_validate(dumped)
        assert isinstance(restored.config, RichConfig)
        assert restored.config.prompt == original.config.prompt  # type: ignore[attr-defined]
        assert restored.config.model_name == original.config.model_name  # type: ignore[attr-defined]
        assert restored.config.tools == original.config.tools  # type: ignore[attr-defined]
        assert restored.role == original.role
        assert restored.skills == original.skills
