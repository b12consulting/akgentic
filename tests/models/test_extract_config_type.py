"""Tests for _extract_config_type with real agent class hierarchy."""

from __future__ import annotations

import pytest
from akgentic.core.agent import Akgent
from akgentic.core.agent_config import BaseConfig
from akgentic.core.agent_state import BaseState

from akgentic.catalog.models.agent import _extract_config_type


class CustomConfig(BaseConfig):
    custom_field: str = "test"


class SimpleAgent(Akgent[BaseConfig, BaseState]):
    pass


class CustomConfigAgent(Akgent[CustomConfig, BaseState]):
    pass


class IntermediateBase(Akgent[CustomConfig, BaseState]):
    """Intermediate base that declares generic params."""

    pass


class DerivedAgent(IntermediateBase):
    """Derived agent that does NOT re-declare generics."""

    pass


class TestExtractConfigType:
    def test_simple_agent_returns_base_config(self) -> None:
        result = _extract_config_type(SimpleAgent)
        assert result is BaseConfig

    def test_custom_config_agent(self) -> None:
        result = _extract_config_type(CustomConfigAgent)
        assert result is CustomConfig

    def test_derived_agent_resolves_through_mro(self) -> None:
        result = _extract_config_type(DerivedAgent)
        assert result is CustomConfig

    def test_raises_for_non_agent_class(self) -> None:
        class NotAnAgent:
            pass

        with pytest.raises(ValueError, match="does not parameterize Akgent"):
            _extract_config_type(NotAnAgent)

    def test_raises_for_raw_akgent(self) -> None:
        # Akgent itself doesn't parameterize Akgent[X, Y] in __orig_bases__
        with pytest.raises(ValueError, match="does not parameterize Akgent"):
            _extract_config_type(Akgent)
