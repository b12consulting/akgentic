"""Tests for Akgent.init_llm_context() no-op method (Story 10.1)."""

from collections.abc import Generator
from typing import Any

import pykka
import pytest

from akgentic.core.agent import Akgent
from akgentic.core.agent_config import BaseConfig
from akgentic.core.agent_state import BaseState


@pytest.fixture(autouse=True)
def cleanup_actors() -> Generator[None, None, None]:
    """Ensure all actors are stopped after each test."""
    yield
    pykka.ActorRegistry.stop_all()


class _TestAgent(Akgent[BaseConfig, BaseState]):
    """Minimal Akgent subclass for testing."""

    pass


class TestInitLlmContext:
    """Verify init_llm_context() no-op behavior on Akgent base class."""

    def test_method_exists_on_akgent(self) -> None:
        """init_llm_context is a public method on Akgent."""
        assert hasattr(Akgent, "init_llm_context")
        assert callable(getattr(Akgent, "init_llm_context"))

    def test_accepts_empty_list_and_returns_none(self) -> None:
        """Calling init_llm_context([]) returns None without error."""
        config = BaseConfig(name="test-agent", role="Agent")
        ref = _TestAgent.start(config=config)
        proxy = ref.proxy()

        result = proxy.init_llm_context([]).get()

        assert result is None
        ref.stop()

    def test_accepts_non_empty_list_without_error(self) -> None:
        """Calling init_llm_context with message-like dicts returns None."""
        config = BaseConfig(name="test-agent", role="Agent")
        ref = _TestAgent.start(config=config)
        proxy = ref.proxy()

        messages: list[Any] = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        result = proxy.init_llm_context(messages).get()

        assert result is None
        ref.stop()

    def test_callable_through_proxy(self) -> None:
        """init_llm_context is callable through Pykka proxy dispatch."""
        config = BaseConfig(name="test-proxy", role="Agent")
        ref = _TestAgent.start(config=config)
        proxy = ref.proxy()

        # Should not raise any exception
        proxy.init_llm_context([{"role": "user", "content": "test"}]).get()

        ref.stop()
