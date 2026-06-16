"""Unit tests for ReactAgent implementation."""

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest
from pydantic_ai import BinaryContent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelRequest, SystemPromptPart, UserPromptPart

from akgentic.llm import (
    ModelConfig,
    ReactAgent,
    ReactAgentConfig,
    UsageLimitError,
    UsageLimits,
    UserPrompt,
)
from akgentic.llm.event import (
    LlmMessageEvent,
    LlmSystemPromptEvent,
    SystemPromptPartSnapshot,
    ToolCallEvent,
)


class MockObserver:
    """Mock observer for context notifications."""

    def __init__(self):
        self.events = []

    def notify_event(self, event: object) -> None:
        self.events.append(event)


@pytest.fixture
def minimal_config():
    """Minimal ReactAgentConfig for testing."""
    return ReactAgentConfig(
        model_cfg=ModelConfig(provider="openai", model="gpt-4o"),
    )


@pytest.fixture
def config_with_limits():
    """ReactAgentConfig with usage limits."""
    return ReactAgentConfig(
        model_cfg=ModelConfig(provider="openai", model="gpt-4o"),
        usage_limits=UsageLimits(request_limit=5, total_tokens_limit=1000),
    )


@pytest.fixture
def mock_agent_iter():
    """Mock pydantic-ai Agent.iter() for testing."""

    async def fake_iter(*args, **kwargs):
        # Create mock run object that supports async context manager
        class MockRun:
            def __init__(self):
                self.result = MagicMock(output="test result")
                self._messages = [ModelRequest(parts=[UserPromptPart(content="test")])]

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                return False

            def __aiter__(self):
                return self

            async def __anext__(self):
                # Yield once then stop
                if not hasattr(self, "_iterated"):
                    self._iterated = True
                    return None
                raise StopAsyncIteration

            def all_messages(self):
                return self._messages

        return MockRun()

    return fake_iter


class TestUsageLimitError:
    """Test UsageLimitError exception."""

    def test_exception_creation(self):
        """Test UsageLimitError can be raised."""
        with pytest.raises(UsageLimitError):
            raise UsageLimitError("Limit exceeded")

    def test_exception_message(self):
        """Test UsageLimitError preserves message."""
        try:
            raise UsageLimitError("Request limit reached")
        except UsageLimitError as e:
            assert str(e) == "Request limit reached"


class TestReactAgentInit:
    """Test ReactAgent initialization."""

    def test_init_minimal_config(self, minimal_config):
        """Test agent initializes with minimal config."""
        agent = ReactAgent(config=minimal_config)
        assert agent is not None
        assert agent.context is not None
        assert agent.pydantic_agent is not None

    def test_init_with_deps_type(self, minimal_config):
        """Test agent initializes with deps_type."""

        class MyDeps:
            value: str = "test"

        agent = ReactAgent(config=minimal_config, deps_type=MyDeps)
        assert agent is not None

    def test_init_with_tools(self, minimal_config):
        """Test agent initializes with tools."""

        def my_tool(query: str) -> str:
            return f"Result: {query}"

        agent = ReactAgent(config=minimal_config, tools=[my_tool])
        assert agent is not None

    def test_init_with_result_type(self, minimal_config):
        """Test agent initializes with custom result_type (Pydantic model)."""
        from pydantic import BaseModel

        class CustomResult(BaseModel):
            value: str

        agent = ReactAgent(config=minimal_config, result_type=CustomResult)
        assert agent is not None


class TestReactAgentRun:
    """Test ReactAgent.run() method."""

    @pytest.mark.asyncio
    async def test_run_returns_result(self, minimal_config):
        """Test run() returns result from pydantic-ai agent."""
        agent = ReactAgent(config=minimal_config)

        # Create mock run object
        class MockRun:
            def __init__(self):
                self.result = MagicMock(output="test result")
                self._new_messages = [ModelRequest(parts=[UserPromptPart(content="test")])]

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                return False

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not hasattr(self, "_iterated"):
                    self._iterated = True
                    return None
                raise StopAsyncIteration

            def new_messages(self):
                return self._new_messages

        # Patch iter to return context manager directly
        with patch.object(agent._pydantic_agent, "iter", return_value=MockRun()):
            result = await agent.run("test query")
            assert result == "test result"

    @pytest.mark.asyncio
    async def test_run_updates_context(self, minimal_config):
        """Test context messages updated after run()."""
        agent = ReactAgent(config=minimal_config)

        # Create mock run object
        class MockRun:
            def __init__(self):
                self.result = MagicMock(output="test result")
                self._new_messages = [ModelRequest(parts=[UserPromptPart(content="test")])]

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                return False

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not hasattr(self, "_iterated"):
                    self._iterated = True
                    return None
                raise StopAsyncIteration

            def new_messages(self):
                return self._new_messages

        assert len(agent.context.messages) == 0

        with patch.object(agent._pydantic_agent, "iter", return_value=MockRun()):
            await agent.run("test query")

            # Context should have messages after run
            assert len(agent.context.messages) == 1

    @pytest.mark.asyncio
    async def test_usage_limit_error_raised(self, minimal_config):
        """Test UsageLimitError raised when pydantic-ai raises UsageLimitExceeded."""
        agent = ReactAgent(config=minimal_config)

        class FailingRun:
            async def __aenter__(self):
                raise UsageLimitExceeded("Request limit exceeded")

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                return False

        with patch.object(agent._pydantic_agent, "iter", return_value=FailingRun()):
            with pytest.raises(UsageLimitError) as exc_info:
                await agent.run("test query")
            assert "Request limit exceeded" in str(exc_info.value)


class TestReactAgentProperties:
    """Test ReactAgent properties and methods."""

    def test_context_property(self, minimal_config):
        """Test context property returns ContextManager."""
        agent = ReactAgent(config=minimal_config)
        context = agent.context
        assert context is not None
        assert hasattr(context, "messages")
        assert hasattr(context, "checkpoint")

    def test_pydantic_agent_property(self, minimal_config):
        """Test pydantic_agent property returns pydantic-ai Agent."""
        agent = ReactAgent(config=minimal_config)
        pydantic_agent = agent.pydantic_agent
        assert pydantic_agent is not None
        # Check it has pydantic-ai Agent methods
        assert hasattr(pydantic_agent, "tool")
        assert hasattr(pydantic_agent, "system_prompt")


class TestReactAgentContextMethods:
    """Test ReactAgent context management methods."""

    @pytest.mark.asyncio
    async def test_subscribe_context(self, minimal_config):
        """Test subscribe_context() observer notified on message add."""
        observer = MockObserver()
        agent = ReactAgent(config=minimal_config)

        # Create mock run object
        class MockRun:
            def __init__(self):
                self.result = MagicMock(output="test result")
                self._new_messages = [ModelRequest(parts=[UserPromptPart(content="test")])]

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                return False

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not hasattr(self, "_iterated"):
                    self._iterated = True
                    return None
                raise StopAsyncIteration

            def new_messages(self):
                return self._new_messages

        agent.subscribe_context(observer)

        with patch.object(agent._pydantic_agent, "iter", return_value=MockRun()):
            await agent.run("test query")

            # Observer should have been notified
            assert len(observer.events) == 1

    def test_checkpoint_creates_snapshot(self, minimal_config):
        """Test checkpoint() creates snapshot."""
        agent = ReactAgent(config=minimal_config)
        snapshot = agent.checkpoint("test-checkpoint")
        assert snapshot is not None
        assert snapshot.checkpoint_id == "test-checkpoint"

    def test_rewind_restores_context(self, minimal_config):
        """Test rewind() restores context."""
        agent = ReactAgent(config=minimal_config)

        # Create checkpoint
        snapshot = agent.checkpoint("before")
        assert snapshot.checkpoint_id == "before"

        # Modify context (add a message)
        test_message = ModelRequest(parts=[UserPromptPart(content="test")])
        agent.context.add_message(test_message)
        assert len(agent.context.messages) == 1

        # Rewind
        agent.rewind("before")
        assert len(agent.context.messages) == 0


class TestReactAgentSyncMethod:
    """Test ReactAgent.run_sync() method."""

    def test_run_sync_works_synchronously(self, minimal_config):
        """Test run_sync() executes synchronously."""
        agent = ReactAgent(config=minimal_config)

        # Create mock run object
        class MockRun:
            def __init__(self):
                self.result = MagicMock(output="test result")
                self._new_messages = [ModelRequest(parts=[UserPromptPart(content="test")])]

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                return False

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not hasattr(self, "_iterated"):
                    self._iterated = True
                    return None
                raise StopAsyncIteration

            def new_messages(self):
                return self._new_messages

        with patch.object(agent._pydantic_agent, "iter", return_value=MockRun()):
            result = agent.run_sync("test query")
            assert result == "test result"


class TestReactAgentSystemPrompts:
    """Test system prompt registration."""

    def test_current_datetime_prompt_registered(self, minimal_config):
        """Test current_datetime_prompt always registered."""
        agent = ReactAgent(config=minimal_config)
        # Datetime prompt is always registered
        # We verify by checking agent initialized without error
        assert agent is not None

    def test_system_prompt_decorator_wrapper(self, minimal_config):
        """Test system_prompt() decorator wrapper registers custom prompt."""
        agent = ReactAgent(config=minimal_config)

        # Register custom prompt
        @agent.system_prompt
        def custom_prompt(ctx):
            return "Custom system prompt"

        # Verify decorator returned a callable
        assert callable(custom_prompt)


class TestReactAgentToolDecorator:
    """Test tool decorator wrapper."""

    def test_tool_decorator_wrapper(self, minimal_config):
        """Test tool() decorator wrapper registers tool function."""

        # Create agent with deps_type to satisfy pydantic-ai requirement
        class MyDeps:
            pass

        agent = ReactAgent(config=minimal_config, deps_type=MyDeps)

        # Register tool with proper RunContext annotation
        from pydantic_ai import RunContext

        @agent.tool
        def search_tool(ctx: RunContext[MyDeps], query: str) -> list[str]:
            """Search for items matching query.

            Args:
                query: The search query string

            Returns:
                List of matching items
            """
            return [f"Result: {query}"]

        # Verify decorator returned a callable
        assert callable(search_tool)


class TestReactAgentUsageLimits:
    """Test usage limits conversion."""

    def test_usage_limits_converted(self, config_with_limits):
        """Test usage limits from config converted to pydantic-ai format."""
        agent = ReactAgent(config=config_with_limits)
        # Test that conversion happens without error
        pydantic_limits = agent._to_pydantic_limits(config_with_limits.usage_limits)
        assert pydantic_limits is not None
        assert pydantic_limits.request_limit == 5
        assert pydantic_limits.total_tokens_limit == 1000

    def test_none_usage_limits(self, minimal_config):
        """Test None usage limits returns None."""
        agent = ReactAgent(config=minimal_config)
        pydantic_limits = agent._to_pydantic_limits(None)
        assert pydantic_limits is None


class TestReactAgentMultimodalPrompt:
    """Test ReactAgent multimodal UserPrompt support."""

    def test_str_prompt_passes_through(self, minimal_config):
        """Test str user_prompt passes through to pydantic-ai unchanged."""
        agent = ReactAgent(config=minimal_config)
        captured_kwargs: dict = {}

        class MockRun:
            def __init__(self, *args, **kwargs):
                captured_kwargs.update(kwargs)
                self.result = MagicMock(output="ok")
                self._new_messages = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

            def new_messages(self):
                return self._new_messages

        with patch.object(agent._pydantic_agent, "iter", side_effect=MockRun):
            agent.run_sync("plain text")

        assert captured_kwargs["user_prompt"] == "plain text"

    def test_list_prompt_passes_through_unchanged(self, minimal_config):
        """Test list[str | BinaryContent] passes to pydantic-ai unchanged."""
        agent = ReactAgent(config=minimal_config)
        captured_kwargs: dict = {}
        multimodal = ["describe: ", BinaryContent(data=b"imgbytes", media_type="image/png")]

        class MockRun:
            def __init__(self, *args, **kwargs):
                captured_kwargs.update(kwargs)
                self.result = MagicMock(output="ok")
                self._new_messages = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

            def new_messages(self):
                return self._new_messages

        with patch.object(agent._pydantic_agent, "iter", side_effect=MockRun):
            agent.run_sync(multimodal)

        assert captured_kwargs["user_prompt"] is multimodal  # exact same object, no copy

    def test_user_prompt_importable(self):
        """Test UserPrompt type alias importable from akgentic.llm."""
        from akgentic.llm import UserPrompt as UP

        assert UP is not None

    def test_user_prompt_alias_in_module_scope(self):
        """Test UserPrompt imported at top of test file is not None."""
        assert UserPrompt is not None

    def test_user_prompt_is_union_type(self):
        """Test UserPrompt type alias resolves to a union containing str and list."""
        import types

        # UserPrompt = str | list[str | BinaryContent] is a UnionType in Python 3.10+
        assert isinstance(UserPrompt, types.UnionType)
        # Both str and list must be args of the union
        union_args = UserPrompt.__args__
        assert str in union_args
        # list type should be present (as a generic alias)
        list_args = [a for a in union_args if hasattr(a, "__origin__") and a.__origin__ is list]
        assert len(list_args) == 1

    def test_no_conversion_in_run(self, minimal_config):
        """Test no BinaryContent construction or list conversion inside run()."""
        agent = ReactAgent(config=minimal_config)
        captured_kwargs: dict = {}
        # Use a list prompt to verify it passes through as-is (same identity)
        bc = BinaryContent(data=b"x", media_type="image/png")
        multimodal: list[str | BinaryContent] = ["text", bc]

        class MockRun:
            def __init__(self, *args, **kwargs):
                captured_kwargs.update(kwargs)
                self.result = MagicMock(output="ok")
                self._new_messages = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

            def new_messages(self):
                return self._new_messages

        with patch.object(agent._pydantic_agent, "iter", side_effect=MockRun):
            agent.run_sync(multimodal)

        # The exact same list object must be passed — no copy, no wrapping
        assert captured_kwargs["user_prompt"] is multimodal


# --- Helper event wrappers for restore_context tests ---


@dataclass
class FakeEventMessage:
    """Mimics EventMessage from akgentic-core with an .event payload."""

    event: object


class TestReactAgentFoldsPendingOperatorActions:
    """ReactAgent.run folds buffered pre-first-run operator actions (FR4, FR5)."""

    @staticmethod
    def _capturing_run_factory(captured: dict):
        """Return a MockRun class that records the kwargs passed to iter()."""

        class MockRun:
            def __init__(self, *args, **kwargs):
                captured.update(kwargs)
                self.result = MagicMock(output="ok")
                self._new_messages: list = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

            def new_messages(self):
                return self._new_messages

        return MockRun

    @pytest.mark.asyncio
    async def test_str_prompt_gets_preamble_prepended(self, minimal_config):
        """FR4: a buffered entry is prepended to a str prompt with a blank-line join."""
        agent = ReactAgent(config=minimal_config)
        agent.context.record_operator_action("[Operator action] ran /reset")
        captured: dict = {}

        with patch.object(
            agent._pydantic_agent, "iter", side_effect=self._capturing_run_factory(captured)
        ):
            await agent.run("hello")

        assert captured["user_prompt"] == "[Operator action] ran /reset\n\nhello"

    @pytest.mark.asyncio
    async def test_multiple_entries_joined_in_order(self, minimal_config):
        """FR4: multiple buffered entries join with blank lines, then the prompt."""
        agent = ReactAgent(config=minimal_config)
        agent.context.record_operator_action("first")
        agent.context.record_operator_action("second")
        captured: dict = {}

        with patch.object(
            agent._pydantic_agent, "iter", side_effect=self._capturing_run_factory(captured)
        ):
            await agent.run("body")

        assert captured["user_prompt"] == "first\n\nsecond\n\nbody"

    @pytest.mark.asyncio
    async def test_list_prompt_gets_preamble_as_leading_element(self, minimal_config):
        """FR4: a multimodal list prompt gets the preamble inserted as its first element."""
        agent = ReactAgent(config=minimal_config)
        agent.context.record_operator_action("op-A")
        agent.context.record_operator_action("op-B")
        bc = BinaryContent(data=b"img", media_type="image/png")
        multimodal: list = ["describe", bc]
        captured: dict = {}

        with patch.object(
            agent._pydantic_agent, "iter", side_effect=self._capturing_run_factory(captured)
        ):
            await agent.run(multimodal)

        assert captured["user_prompt"] == ["op-A\n\nop-B", "describe", bc]

    @pytest.mark.asyncio
    async def test_run_clears_buffer(self, minimal_config):
        """FR4: after a run the operator-action buffer is empty."""
        agent = ReactAgent(config=minimal_config)
        agent.context.record_operator_action("once")
        captured: dict = {}

        with patch.object(
            agent._pydantic_agent, "iter", side_effect=self._capturing_run_factory(captured)
        ):
            await agent.run("q")

        assert agent.context.drain_pending_operator_actions() == []

    @pytest.mark.asyncio
    async def test_empty_buffer_leaves_str_prompt_unchanged(self, minimal_config):
        """FR4: with nothing buffered, a str prompt passes through unchanged."""
        agent = ReactAgent(config=minimal_config)
        captured: dict = {}

        with patch.object(
            agent._pydantic_agent, "iter", side_effect=self._capturing_run_factory(captured)
        ):
            await agent.run("plain")

        assert captured["user_prompt"] == "plain"

    @pytest.mark.asyncio
    async def test_empty_buffer_leaves_list_prompt_identical(self, minimal_config):
        """FR4: with nothing buffered, a list prompt passes through as the same object."""
        agent = ReactAgent(config=minimal_config)
        multimodal: list = ["text", BinaryContent(data=b"x", media_type="image/png")]
        captured: dict = {}

        with patch.object(
            agent._pydantic_agent, "iter", side_effect=self._capturing_run_factory(captured)
        ):
            await agent.run(multimodal)

        assert captured["user_prompt"] is multimodal

    @pytest.mark.asyncio
    async def test_message_history_stays_empty_so_system_prompt_injects(self, minimal_config):
        """FR5: folding leaves message_history empty so pydantic-ai injects the system prompt."""
        agent = ReactAgent(config=minimal_config)
        agent.context.record_operator_action("[Operator action] ran /help")
        captured: dict = {}

        with patch.object(
            agent._pydantic_agent, "iter", side_effect=self._capturing_run_factory(captured)
        ):
            await agent.run("question")

        # message_history is the drain-independent run buffer — empty before the
        # first run, so pydantic-ai's `if not messages` injection path runs.
        assert captured["message_history"] == []


class TestReactAgentRestoreContext:
    """Test ReactAgent.restore_context() method."""

    def test_filters_llm_message_events(self, minimal_config):
        """Test restore_context filters LlmMessageEvent from mixed event list."""
        agent = ReactAgent(config=minimal_config)

        msg1 = ModelRequest(parts=[UserPromptPart(content="Hello")])
        msg2 = ModelRequest(parts=[UserPromptPart(content="World")])

        events = [
            FakeEventMessage(event=LlmMessageEvent(message=msg1)),
            FakeEventMessage(
                event=ToolCallEvent(
                    run_id="r1", tool_name="t", tool_call_id="c1", arguments="{}"
                )
            ),
            FakeEventMessage(event=LlmMessageEvent(message=msg2)),
        ]

        agent.restore_context(events)

        assert len(agent.context.messages) == 2
        assert agent.context.messages[0] is msg1
        assert agent.context.messages[1] is msg2

    def test_ignores_non_llm_events(self, minimal_config):
        """Test restore_context ignores non-LlmMessageEvent events."""
        agent = ReactAgent(config=minimal_config)

        events = [
            FakeEventMessage(
                event=ToolCallEvent(
                    run_id="r1", tool_name="t", tool_call_id="c1", arguments="{}"
                )
            ),
            FakeEventMessage(event="arbitrary string"),
            "not even an event message",
        ]

        agent.restore_context(events)

        assert len(agent.context.messages) == 0

    def test_handles_empty_event_list(self, minimal_config):
        """Test restore_context handles empty event list gracefully."""
        agent = ReactAgent(config=minimal_config)

        # Pre-populate context to verify it gets cleared/replaced
        agent.context.add_message(ModelRequest(parts=[UserPromptPart(content="pre")]))
        assert len(agent.context.messages) == 1

        agent.restore_context([])

        assert len(agent.context.messages) == 0

    def test_handles_zero_llm_events(self, minimal_config):
        """Test restore_context handles list with zero LlmMessageEvent events."""
        agent = ReactAgent(config=minimal_config)

        agent.context.add_message(ModelRequest(parts=[UserPromptPart(content="pre")]))

        events = [
            FakeEventMessage(
                event=ToolCallEvent(
                    run_id="r1", tool_name="t", tool_call_id="c1", arguments="{}"
                )
            ),
        ]

        agent.restore_context(events)

        # Should restore empty list (no LlmMessageEvents found)
        assert len(agent.context.messages) == 0

    def test_preserves_message_order(self, minimal_config):
        """Test restore_context preserves original order of LlmMessageEvent messages."""
        agent = ReactAgent(config=minimal_config)

        msgs = [ModelRequest(parts=[UserPromptPart(content=f"msg-{i}")]) for i in range(5)]
        events = [FakeEventMessage(event=LlmMessageEvent(message=m)) for m in msgs]

        agent.restore_context(events)

        assert len(agent.context.messages) == 5
        for i, m in enumerate(agent.context.messages):
            assert m.parts[0].content == f"msg-{i}"  # type: ignore[attr-defined]


# --- System prompt rendering events: run-lifecycle wiring (Story 6-2) ---


def _system_request_with_run_id(
    *system_parts: tuple[str | None, str],
    run_id: str = "run-1",
) -> ModelRequest:
    """Build a first ModelRequest with system parts + a user part and a run_id.

    Mirrors the shape pydantic-ai stamps on a run's first ModelRequest: one
    SystemPromptPart per (dynamic_ref, content) pair, a trailing UserPromptPart,
    and the run's run_id set on the message.
    """
    parts: list[object] = [
        SystemPromptPart(content=content, dynamic_ref=dynamic_ref)
        for dynamic_ref, content in system_parts
    ]
    parts.append(UserPromptPart(content="Hello"))
    return ModelRequest(parts=parts, run_id=run_id)  # type: ignore[arg-type]


def _make_mock_run(new_messages: list[ModelRequest]):
    """Return a MockRun instance whose new_messages() yields `new_messages`."""

    class MockRun:
        def __init__(self) -> None:
            self.result = MagicMock(output="ok")
            self._new_messages = new_messages

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not hasattr(self, "_iterated"):
                self._iterated = True
                return None
            raise StopAsyncIteration

        def new_messages(self):
            return self._new_messages

    return MockRun()


def _system_events(observer: MockObserver) -> list[LlmSystemPromptEvent]:
    """Filter an observer's captured events to LlmSystemPromptEvent instances."""
    return [e for e in observer.events if isinstance(e, LlmSystemPromptEvent)]


class TestReactAgentRunRecordsSystemPrompt:
    """Per-run system prompt recording wired into ReactAgent.run() (AC 1, 2, 3)."""

    @pytest.mark.asyncio
    async def test_run_records_one_event_with_run_id(self, minimal_config):
        """AC 1/2: one LlmSystemPromptEvent emitted, run_id matches the run's messages."""
        observer = MockObserver()
        agent = ReactAgent(config=minimal_config, observer=observer)

        run = _make_mock_run([_system_request_with_run_id(("backstory", "B."), run_id="abc")])
        with patch.object(agent._pydantic_agent, "iter", return_value=run):
            await agent.run("query")

        events = _system_events(observer)
        assert len(events) == 1
        assert events[0].run_id == "abc"

    @pytest.mark.asyncio
    async def test_dedup_across_two_unchanged_runs(self, minimal_config):
        """AC 3: two runs with identical rendering emit exactly one event total."""
        observer = MockObserver()
        agent = ReactAgent(config=minimal_config, observer=observer)

        run1 = _make_mock_run(
            [_system_request_with_run_id(("backstory", "B."), run_id="r1")]
        )
        with patch.object(agent._pydantic_agent, "iter", return_value=run1):
            await agent.run("query 1")

        run2 = _make_mock_run(
            [_system_request_with_run_id(("backstory", "B."), run_id="r2")]
        )
        with patch.object(agent._pydantic_agent, "iter", return_value=run2):
            await agent.run("query 2")

        assert len(_system_events(observer)) == 1

    @pytest.mark.asyncio
    async def test_changed_rendering_emits_second_event(self, minimal_config):
        """AC 2: a changed current_date block emits a second, distinct event."""
        observer = MockObserver()
        agent = ReactAgent(config=minimal_config, observer=observer)

        run1 = _make_mock_run(
            [_system_request_with_run_id(("current_date", "Day 1."), run_id="r1")]
        )
        with patch.object(agent._pydantic_agent, "iter", return_value=run1):
            await agent.run("query 1")

        # Simulate pydantic-ai's in-place re-evaluation by mutating the first
        # request's system part content before the next run.
        first_request = agent.context.messages[0]
        first_request.parts[0].content = "Day 2."  # type: ignore[union-attr]

        run2 = _make_mock_run(
            [ModelRequest(parts=[UserPromptPart(content="more")], run_id="r2")]
        )
        with patch.object(agent._pydantic_agent, "iter", return_value=run2):
            await agent.run("query 2")

        events = _system_events(observer)
        assert len(events) == 2
        assert events[0].content_hash != events[1].content_hash

    @pytest.mark.asyncio
    async def test_no_new_messages_records_nothing(self, minimal_config):
        """AC 1 edge: a run with no new messages records no event (no run_id)."""
        observer = MockObserver()
        agent = ReactAgent(config=minimal_config, observer=observer)

        run = _make_mock_run([])
        with patch.object(agent._pydantic_agent, "iter", return_value=run):
            await agent.run("query")

        assert _system_events(observer) == []

    @pytest.mark.asyncio
    async def test_messages_without_run_id_record_nothing(self, minimal_config):
        """AC 1 edge: new messages lacking a run_id skip the recording call."""
        observer = MockObserver()
        agent = ReactAgent(config=minimal_config, observer=observer)

        run = _make_mock_run(
            [_system_request_with_run_id(("backstory", "B."), run_id=None)]  # type: ignore[arg-type]
        )
        with patch.object(agent._pydantic_agent, "iter", return_value=run):
            await agent.run("query")

        assert _system_events(observer) == []


class TestReactAgentRestoreSeedsSystemPromptHash:
    """restore_context() seeds the dedup hash from persisted events (AC 4, 5, 6, 7)."""

    def test_seed_from_persisted_event(self, minimal_config):
        """AC 4: the seeded hash equals the persisted event's content_hash."""
        agent = ReactAgent(config=minimal_config)

        event = LlmSystemPromptEvent(
            run_id="r1",
            parts=(SystemPromptPartSnapshot(dynamic_ref="b", content="B."),),
            content_hash="abc123",
        )
        agent.restore_context([FakeEventMessage(event=event)])

        assert agent.context._last_system_prompt_hash == "abc123"

    def test_seed_suppresses_unchanged_reemission(self, minimal_config):
        """AC 4: a run matching the seeded rendering emits nothing."""
        observer = MockObserver()
        agent = ReactAgent(config=minimal_config, observer=observer)

        # Compute the real hash for ("backstory", "B.") via a throwaway manager run.
        probe = ReactAgent(config=minimal_config)
        probe.context.add_message(_system_request_with_run_id(("backstory", "B."), run_id="r0"))
        probe.context.record_system_prompt("r0")
        known_hash = probe.context._last_system_prompt_hash

        event = LlmSystemPromptEvent(
            run_id="r1",
            parts=(SystemPromptPartSnapshot(dynamic_ref="backstory", content="B."),),
            content_hash=known_hash,
        )
        agent.restore_context([FakeEventMessage(event=event)])

        run = _make_mock_run(
            [_system_request_with_run_id(("backstory", "B."), run_id="r2")]
        )
        with patch.object(agent._pydantic_agent, "iter", return_value=run):
            agent.run_sync("query")

        assert _system_events(observer) == []

    def test_post_restore_change_emits(self, minimal_config):
        """AC 5: a run whose rendering differs from the seed emits one event."""
        observer = MockObserver()
        agent = ReactAgent(config=minimal_config, observer=observer)

        event = LlmSystemPromptEvent(
            run_id="r1",
            parts=(SystemPromptPartSnapshot(dynamic_ref="backstory", content="Old."),),
            content_hash="seeded-hash-that-differs",
        )
        agent.restore_context([FakeEventMessage(event=event)])

        run = _make_mock_run(
            [_system_request_with_run_id(("backstory", "New."), run_id="r2")]
        )
        with patch.object(agent._pydantic_agent, "iter", return_value=run):
            agent.run_sync("query")

        assert len(_system_events(observer)) == 1

    def test_latest_event_wins_on_restore(self, minimal_config):
        """AC 4: with two persisted events, the later one's hash is seeded."""
        agent = ReactAgent(config=minimal_config)

        first = LlmSystemPromptEvent(run_id="r1", parts=(), content_hash="first-hash")
        second = LlmSystemPromptEvent(run_id="r2", parts=(), content_hash="second-hash")
        agent.restore_context(
            [FakeEventMessage(event=first), FakeEventMessage(event=second)]
        )

        assert agent.context._last_system_prompt_hash == "second-hash"

    def test_no_persisted_event_leaves_hash_none(self, minimal_config):
        """AC 6: only LlmMessageEvents present ⇒ dedup hash stays None."""
        agent = ReactAgent(config=minimal_config)

        msg = ModelRequest(parts=[UserPromptPart(content="Hi")])
        agent.restore_context([FakeEventMessage(event=LlmMessageEvent(message=msg))])

        assert agent.context._last_system_prompt_hash is None

    def test_pre_event_history_first_run_emits(self, minimal_config):
        """AC 6: after a seed-less restore, the first run emits the None → hash event.

        An older team persisted its run-1 messages (whose first ModelRequest
        carries the system parts) but never persisted an LlmSystemPromptEvent, so
        restore leaves the dedup hash at None and the next record emits.
        """
        observer = MockObserver()
        agent = ReactAgent(config=minimal_config, observer=observer)

        # Pre-event history: a persisted first ModelRequest with system parts,
        # but no LlmSystemPromptEvent to seed from.
        msg = _system_request_with_run_id(("backstory", "B."), run_id="r1")
        agent.restore_context([FakeEventMessage(event=LlmMessageEvent(message=msg))])
        assert agent.context._last_system_prompt_hash is None

        run = _make_mock_run(
            [ModelRequest(parts=[UserPromptPart(content="more")], run_id="r2")]
        )
        with patch.object(agent._pydantic_agent, "iter", return_value=run):
            agent.run_sync("query")

        assert len(_system_events(observer)) == 1

    def test_messages_still_restored_with_seed(self, minimal_config):
        """AC 7: message restore is unchanged when a seed event is also present."""
        agent = ReactAgent(config=minimal_config)

        msg1 = ModelRequest(parts=[UserPromptPart(content="Hello")])
        msg2 = ModelRequest(parts=[UserPromptPart(content="World")])
        seed_event = LlmSystemPromptEvent(run_id="r1", parts=(), content_hash="abc")

        agent.restore_context(
            [
                FakeEventMessage(event=LlmMessageEvent(message=msg1)),
                FakeEventMessage(event=seed_event),
                FakeEventMessage(event=LlmMessageEvent(message=msg2)),
            ]
        )

        assert len(agent.context.messages) == 2
        assert agent.context.messages[0] is msg1
        assert agent.context.messages[1] is msg2
        assert agent.context._last_system_prompt_hash == "abc"
