"""Test public API exports."""

import akgentic.llm


def test_all_exports_importable():
    """All __all__ members must be importable."""
    for name in akgentic.llm.__all__:
        assert hasattr(akgentic.llm, name), f"{name} missing from akgentic.llm"


def test_no_unexpected_exports():
    """Star import should only include __all__ members."""
    # Submodules are always visible via dir() but not in star imports
    submodules = {"agent", "config", "context", "event", "pricing", "prompts", "providers"}
    exported = {name for name in dir(akgentic.llm) if not name.startswith("_")}
    exported_without_submodules = exported - submodules
    expected = set(akgentic.llm.__all__)
    # __version__ might not always be in dir() depending on how it's defined
    assert exported_without_submodules == expected, (
        f"Unexpected exports: {exported_without_submodules - expected}"
    )


def test_key_exports_present():
    """Verify all key classes and functions are exported."""
    # Configuration
    assert hasattr(akgentic.llm, "ModelConfig")
    assert hasattr(akgentic.llm, "UsageLimits")
    assert hasattr(akgentic.llm, "RuntimeConfig")
    assert hasattr(akgentic.llm, "ReactAgentConfig")

    # Agent
    assert hasattr(akgentic.llm, "ReactAgent")
    assert hasattr(akgentic.llm, "UsageLimitError")

    # Context
    assert hasattr(akgentic.llm, "ContextManager")
    assert hasattr(akgentic.llm, "ContextObserver")
    assert hasattr(akgentic.llm, "ContextSnapshot")

    # Events
    assert hasattr(akgentic.llm, "LlmMessageEvent")
    assert hasattr(akgentic.llm, "LlmCheckpointCreatedEvent")
    assert hasattr(akgentic.llm, "LlmCheckpointRestoredEvent")
    assert hasattr(akgentic.llm, "ToolCallEvent")
    assert hasattr(akgentic.llm, "ToolReturnEvent")

    # Prompts
    assert hasattr(akgentic.llm, "PromptTemplate")
    assert hasattr(akgentic.llm, "current_datetime_prompt")
    assert hasattr(akgentic.llm, "json_output_reminder_prompt")

    # Pricing & Aggregation
    assert hasattr(akgentic.llm, "PRICING")
    assert hasattr(akgentic.llm, "AgentUsageSummary")
    assert hasattr(akgentic.llm, "ModelUsage")
    assert hasattr(akgentic.llm, "RunUsageSummary")
    assert hasattr(akgentic.llm, "aggregate_usage")

    # Providers
    assert hasattr(akgentic.llm, "create_model")
    assert hasattr(akgentic.llm, "create_http_client")
    assert hasattr(akgentic.llm, "create_model_settings")
    assert hasattr(akgentic.llm, "get_output_type")


def test_star_import_works():
    """Test that `from akgentic.llm import *` works correctly."""
    # This simulates star import behavior
    namespace = {}
    exec("from akgentic.llm import *", namespace)

    # Check that all __all__ members are in namespace
    for name in akgentic.llm.__all__:
        assert name in namespace, f"{name} not imported by star import"

    # Check that private names are not imported
    assert "_build_settings" not in namespace
    assert "_create_openai_model" not in namespace


def test_version_exists():
    """Package version should be accessible."""
    assert hasattr(akgentic.llm, "__version__")
    assert isinstance(akgentic.llm.__version__, str)
    assert len(akgentic.llm.__version__) > 0


def test_module_docstring_exists():
    """Module should have a comprehensive docstring."""
    assert akgentic.llm.__doc__ is not None
    assert "Quick Start" in akgentic.llm.__doc__
    assert "Key Concepts" in akgentic.llm.__doc__
    assert "REACT pattern" in akgentic.llm.__doc__


def test_systempromptregistry_not_exported():
    """SystemPromptRegistry should NOT be exported (removed in architecture refactor)."""
    assert "SystemPromptRegistry" not in akgentic.llm.__all__
    assert not hasattr(akgentic.llm, "SystemPromptRegistry")
