"""Tests for BaseAgent media expansion via the command registry.

Tests the registry-driven media expansion pre-step in act() without requiring a
live actor system or real LLM calls. Media expansion resolves the
``_expand_media_refs`` callable from the agent's ``_command_registry`` (guarded by
``registry.has(...)``) and uses its native ``list[str | MediaContent]`` result.
"""

from typing import Any
from unittest.mock import MagicMock

from akgentic.tool.workspace.readers import MediaContent
from pydantic_ai import BinaryContent

from akgentic.agent.agent import BaseAgent
from akgentic.agent.config import AgentConfig
from akgentic.agent.messages import AgentMessage
from akgentic.agent.output_models import StructuredOutput

# =============================================================================
# HELPERS
# =============================================================================


def _make_mock_sender(name: str = "@Human") -> MagicMock:
    """Return a mock ActorAddress with a .name attribute."""
    addr = MagicMock()
    addr.name = name
    return addr


def _make_mock_message(sender_name: str = "@Human") -> MagicMock:
    """Return a mock AgentMessage with sender set."""
    msg = MagicMock(spec=AgentMessage)
    msg.sender = _make_mock_sender(sender_name)
    msg.type = "request"
    return msg


def _make_registry(media_cmd: Any = None) -> MagicMock:
    """Return a mock CommandRegistry for media-expansion tests.

    When ``media_cmd`` is provided, ``has("_expand_media_refs")`` returns True and
    ``callable("_expand_media_refs")`` returns ``media_cmd``. Otherwise ``has(...)``
    returns False (no media command registered).
    """
    registry = MagicMock()
    if media_cmd is None:
        registry.has.return_value = False
    else:
        registry.has.side_effect = lambda name: name == "_expand_media_refs"
        registry.callable.return_value = media_cmd
    return registry


def _make_minimal_agent(name: str = "@TestAgent", media_cmd: Any = None) -> BaseAgent:
    """Construct a BaseAgent-like object without the Pykka actor system.

    Creates a bare BaseAgent instance, bypassing the actor __init__, and
    sets up the minimal attributes needed to call act() in unit tests:
    - _command_registry: mock registry (no media command unless ``media_cmd`` given)
    - _react_agent: MagicMock
    - _current_message: mock AgentMessage
    - config: mock with .name
    """
    agent: BaseAgent = object.__new__(BaseAgent)

    # Minimal attributes expected by act()
    agent._command_registry = _make_registry(media_cmd)  # type: ignore[attr-defined]
    agent._react_agent = MagicMock()  # type: ignore[attr-defined]
    agent._current_message = _make_mock_message()  # type: ignore[attr-defined]

    mock_config = MagicMock(spec=AgentConfig)
    mock_config.name = name
    agent.config = mock_config  # type: ignore[attr-defined]

    return agent


# =============================================================================
# AC-9: act() media expansion via the command registry
# =============================================================================


class TestBaseAgentMediaExpansion:
    """AC-9: act() expansion pre-step driven by the registry — no LLM required."""

    # ------------------------------------------------------------------
    # image token → BinaryContent in run_sync call
    # ------------------------------------------------------------------

    def test_image_token_converted_to_binary_content(self) -> None:
        """MediaContent in expansion result → BinaryContent passed to run_sync."""
        mock_cmd = MagicMock(
            return_value=[
                "describe ",
                MediaContent(data=b"png-bytes", media_type="image/png"),
                " please",
            ]
        )
        agent = _make_minimal_agent(media_cmd=mock_cmd)

        # Capture what run_sync receives
        captured_prompts: list[Any] = []

        def capture_run_sync(prompt: Any, **kwargs: Any) -> str:
            captured_prompts.append(prompt)
            return "mocked response"

        agent._react_agent.run_sync.side_effect = capture_run_sync  # type: ignore[attr-defined]

        agent.act("describe !!photo.png please", output_type=str)

        mock_cmd.assert_called_once_with("describe !!photo.png please")
        assert len(captured_prompts) == 1
        result_prompt = captured_prompts[0]
        assert isinstance(result_prompt, list)
        assert result_prompt[0] == "describe "
        assert isinstance(result_prompt[1], BinaryContent)
        assert result_prompt[1].data == b"png-bytes"
        assert result_prompt[1].media_type == "image/png"
        assert result_prompt[2] == " please"

    # ------------------------------------------------------------------
    # pure text → str passed unchanged
    # ------------------------------------------------------------------

    def test_pure_text_passed_as_str_unchanged(self) -> None:
        """No MediaContent in expansion result → original str passed to run_sync."""
        mock_cmd = MagicMock(return_value=["no special tokens"])
        agent = _make_minimal_agent(media_cmd=mock_cmd)

        captured_prompts: list[Any] = []

        def capture_run_sync(prompt: Any, **kwargs: Any) -> str:
            captured_prompts.append(prompt)
            return "mocked response"

        agent._react_agent.run_sync.side_effect = capture_run_sync  # type: ignore[attr-defined]

        agent.act("no special tokens", output_type=str)

        assert len(captured_prompts) == 1
        result_prompt = captured_prompts[0]
        # Must be the original string, NOT a list
        assert isinstance(result_prompt, str)
        assert result_prompt == "no special tokens"

    # ------------------------------------------------------------------
    # document token → hint string list, no BinaryContent
    # ------------------------------------------------------------------

    def test_document_token_forwards_hint_to_llm(self) -> None:
        """Doc hint strings (no MediaContent) → expanded parts forwarded to run_sync.

        When ExpandMediaRefs returns a list of plain strings (e.g. a PDF hint or error),
        the expanded parts are forwarded to the LLM so it can act on hints/errors.
        """
        hint_list = ["check ", "!!report.pdf[=> Use workspace_read tool]"]
        mock_cmd = MagicMock(return_value=hint_list)
        agent = _make_minimal_agent(media_cmd=mock_cmd)

        captured_prompts: list[Any] = []

        def capture_run_sync(prompt: Any, **kwargs: Any) -> str:
            captured_prompts.append(prompt)
            return "mocked response"

        agent._react_agent.run_sync.side_effect = capture_run_sync  # type: ignore[attr-defined]

        agent.act("check !!report.pdf", output_type=str)

        assert len(captured_prompts) == 1
        result_prompt = captured_prompts[0]
        # Expanded parts forwarded as list so LLM sees the hint
        assert isinstance(result_prompt, list)
        assert result_prompt == ["check ", "!!report.pdf[=> Use workspace_read tool]"]

    # ------------------------------------------------------------------
    # no media command registered → user_content passed as-is
    # ------------------------------------------------------------------

    def test_command_absent_passes_user_content_as_is(self) -> None:
        """registry.has('_expand_media_refs') is False → user_content str unchanged."""
        agent = _make_minimal_agent()  # no media command registered

        captured_prompts: list[Any] = []

        def capture_run_sync(prompt: Any, **kwargs: Any) -> str:
            captured_prompts.append(prompt)
            return "mocked response"

        agent._react_agent.run_sync.side_effect = capture_run_sync  # type: ignore[attr-defined]

        agent.act("look at !!photo.png", output_type=str)

        # callable() must NOT be consulted when has() is False
        agent._command_registry.callable.assert_not_called()  # type: ignore[attr-defined]
        assert len(captured_prompts) == 1
        result_prompt = captured_prompts[0]
        assert isinstance(result_prompt, str)
        assert result_prompt == "look at !!photo.png"

    # ------------------------------------------------------------------
    # act() signature unchanged
    # ------------------------------------------------------------------

    def test_act_signature_unchanged(self) -> None:
        """act(user_content: str, output_type: type[T]) -> T signature intact."""
        import inspect

        sig = inspect.signature(BaseAgent.act)
        params = list(sig.parameters.keys())
        assert params == ["self", "user_content", "output_type"]
        assert sig.parameters["user_content"].annotation is str

    # ------------------------------------------------------------------
    # branch: both True/False paths of any(isinstance(p, MediaContent)...)
    # ------------------------------------------------------------------

    def test_any_media_content_true_branch(self) -> None:
        """Branch coverage: any(isinstance(p, MediaContent)) == True → list built."""
        mixed = [
            "prefix ",
            MediaContent(data=b"data", media_type="image/jpeg"),
        ]
        mock_cmd = MagicMock(return_value=mixed)
        agent = _make_minimal_agent(media_cmd=mock_cmd)

        captured_prompts: list[Any] = []

        def capture_run_sync(prompt: Any, **kwargs: Any) -> str:
            captured_prompts.append(prompt)
            return "ok"

        agent._react_agent.run_sync.side_effect = capture_run_sync  # type: ignore[attr-defined]

        agent.act("prefix !!img.jpg", output_type=str)

        result_prompt = captured_prompts[0]
        assert isinstance(result_prompt, list)
        assert any(isinstance(p, BinaryContent) for p in result_prompt)

    def test_no_refs_passes_original_str(self) -> None:
        """Branch coverage: no !! tokens → parts == [user_content] → str returned."""
        # _expand_media_refs returns [original_str] when no !! tokens are present
        mock_cmd = MagicMock(return_value=["only strings here"])
        agent = _make_minimal_agent(media_cmd=mock_cmd)

        captured_prompts: list[Any] = []

        def capture_run_sync(prompt: Any, **kwargs: Any) -> str:
            captured_prompts.append(prompt)
            return "ok"

        agent._react_agent.run_sync.side_effect = capture_run_sync  # type: ignore[attr-defined]

        agent.act("only strings here", output_type=str)

        result_prompt = captured_prompts[0]
        assert isinstance(result_prompt, str)


# =============================================================================
# AC-1/AC-2: act() reasons against the static StructuredOutput type
# =============================================================================


class TestActUsesStaticStructuredOutput:
    """Story 5-5: act() uses the static StructuredOutput schema, no per-call subclass."""

    def test_act_calls_run_sync_with_static_structured_output(self) -> None:
        """AC-1: act() passes output_type=StructuredOutput to run_sync."""
        agent = _make_minimal_agent(name="@Manager")

        captured_kwargs: dict[str, Any] = {}

        def capture_run_sync(prompt: Any, **kwargs: Any) -> StructuredOutput:
            captured_kwargs.update(kwargs)
            return StructuredOutput()

        agent._react_agent.run_sync.side_effect = capture_run_sync  # type: ignore[attr-defined]

        agent.act("do something", output_type=StructuredOutput)

        assert captured_kwargs["output_type"] is StructuredOutput

    def test_no_build_structured_output_type_method(self) -> None:
        """AC-1: the per-call subclass builder no longer exists on BaseAgent."""
        assert not hasattr(BaseAgent, "_build_structured_output_type")

    def test_recipient_carries_no_enum_in_schema(self) -> None:
        """AC-2: the static Request schema exposes recipient as a plain string (no enum)."""
        request_schema = StructuredOutput.model_json_schema()["$defs"]["Request"]
        recipient = request_schema["properties"]["recipient"]
        assert recipient["type"] == "string"
        assert "enum" not in recipient
