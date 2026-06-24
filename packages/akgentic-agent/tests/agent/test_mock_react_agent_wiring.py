"""Tests for the env-gated LLM-agent selection in ``BaseAgent._build_react_agent``.

When ``AKGENTIC_MOCK_SCENARIO`` names a scenario YAML, ``on_start`` swaps the
real ``ReactAgent`` for the token-free ``MockReactAgent`` (akgentic-llm loadtest),
carrying the scenario path in a config copy's ``model_cfg.model`` field so the
agent's own ``self.config`` is left intact. Unset -> the real ``ReactAgent``.

The mock branch injects a fake ``akgentic.llm.loadtest`` module so the test is
self-contained and does not require the optional ``loadtest`` extra to be
installed.
"""

import sys
import types

import akgentic.agent.agent as agent_module
import pytest
from akgentic.agent.agent import BaseAgent
from akgentic.llm import ReactAgentConfig

ENV_VAR = "AKGENTIC_MOCK_SCENARIO"


def _make_agent() -> BaseAgent:
    """Bare BaseAgent (no Pykka actor system) for ``_build_react_agent`` tests.

    No ``_event_loop`` is wired — ``_build_react_agent`` no longer reads it.
    """
    agent: BaseAgent = object.__new__(BaseAgent)
    return agent


def test_real_react_agent_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset env -> real ReactAgent built with the config passed straight through."""
    monkeypatch.delenv(ENV_VAR, raising=False)

    captured: dict[str, object] = {}

    class _FakeReactAgent:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(agent_module, "ReactAgent", _FakeReactAgent)

    agent = _make_agent()
    config = ReactAgentConfig()
    result = agent._build_react_agent(config, [], [])

    assert isinstance(result, _FakeReactAgent)
    assert captured["config"] is config  # not copied/mutated
    assert captured["observer"] is agent
    assert captured["deps_type"] is BaseAgent


def test_mock_react_agent_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env set -> MockReactAgent built; scenario path smuggled into a config COPY."""
    monkeypatch.setenv(ENV_VAR, "/tmp/sandpile-research.yaml")

    captured: dict[str, object] = {}

    class _FakeMockReactAgent:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    fake_loadtest = types.ModuleType("akgentic.llm.loadtest")
    fake_loadtest.MockReactAgent = _FakeMockReactAgent  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "akgentic.llm.loadtest", fake_loadtest)

    agent = _make_agent()
    config = ReactAgentConfig()  # model_cfg.model defaults to "gpt-5.2"
    result = agent._build_react_agent(config, [], [])

    assert isinstance(result, _FakeMockReactAgent)
    assert captured["observer"] is agent
    # Scenario path lands in the COPY's model field...
    mock_cfg = captured["config"]
    assert isinstance(mock_cfg, ReactAgentConfig)
    assert mock_cfg.model_cfg.model == "/tmp/sandpile-research.yaml"
    # ...while the original config is untouched.
    assert config.model_cfg.model == "gpt-5.2"


def test_blank_env_falls_back_to_real(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty AKGENTIC_MOCK_SCENARIO is treated as unset (real ReactAgent)."""
    monkeypatch.setenv(ENV_VAR, "")

    built: list[str] = []

    class _FakeReactAgent:
        def __init__(self, **kwargs: object) -> None:
            built.append("real")

    monkeypatch.setattr(agent_module, "ReactAgent", _FakeReactAgent)

    agent = _make_agent()
    result = agent._build_react_agent(ReactAgentConfig(), [], [])

    assert isinstance(result, _FakeReactAgent)
    assert built == ["real"]


def test_real_path_omits_event_loop_kwarg(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC #5 (real path): ReactAgent is built with NO ``event_loop`` keyword."""
    monkeypatch.delenv(ENV_VAR, raising=False)

    captured: dict[str, object] = {}

    class _FakeReactAgent:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(agent_module, "ReactAgent", _FakeReactAgent)

    agent = _make_agent()
    agent._build_react_agent(ReactAgentConfig(), [], [])

    assert "event_loop" not in captured


def test_mock_path_omits_event_loop_kwarg(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC #5 (mock path): MockReactAgent is built with NO ``event_loop`` keyword."""
    monkeypatch.setenv(ENV_VAR, "/tmp/sandpile-research.yaml")

    captured: dict[str, object] = {}

    class _FakeMockReactAgent:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    fake_loadtest = types.ModuleType("akgentic.llm.loadtest")
    fake_loadtest.MockReactAgent = _FakeMockReactAgent  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "akgentic.llm.loadtest", fake_loadtest)

    agent = _make_agent()
    agent._build_react_agent(ReactAgentConfig(), [], [])

    assert "event_loop" not in captured
