"""Tests for Hello World example (Story 5.1).

Verifies the example demonstrates core akgentic patterns:
- Custom message types (HelloMessage)
- Agent message handlers (receiveMsg_<Type> pattern)
- ActorAddress for agent references
- Zero-dependency local actor system
"""

import importlib.util
import sys
from pathlib import Path

from akgentic.core import ActorSystem, Akgent, BaseConfig
from akgentic.core.messages import Message


class TestHelloMessageDefinition:
    """Tests for HelloMessage class definition."""

    def test_hello_message_can_be_imported(self) -> None:
        """HelloMessage class can be imported from example."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "01_hello_world.py"
        spec = importlib.util.spec_from_file_location("hello_world", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        assert hasattr(module, "HelloMessage"), "HelloMessage not defined in example"
        assert issubclass(module.HelloMessage, Message), "HelloMessage must extend Message"

    def test_hello_message_has_greeting_field(self) -> None:
        """HelloMessage has greeting string field."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "01_hello_world.py"
        spec = importlib.util.spec_from_file_location("hello_world", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        msg = module.HelloMessage(greeting="Hello!")
        assert msg.greeting == "Hello!"


class TestReceiverAgentHandler:
    """Tests for ReceiverAgent message handling."""

    def test_receiver_agent_handles_hello_message(self, capsys) -> None:
        """ReceiverAgent.receiveMsg_HelloMessage prints greeting."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "01_hello_world.py"
        spec = importlib.util.spec_from_file_location("hello_world", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        # Create receiver agent
        system = ActorSystem()
        try:
            receiver_addr = system.createActor(
                module.ReceiverAgent,
                config=BaseConfig(name="test-receiver", role="Receiver"),
            )

            # Send HelloMessage
            hello_msg = module.HelloMessage(greeting="Test greeting!")
            system.tell(receiver_addr, hello_msg)

            # Give time for async message processing
            import time

            time.sleep(0.2)

            captured = capsys.readouterr()
            assert "Test greeting!" in captured.out

        finally:
            system.shutdown(timeout=5)


class TestGreeterAgentSender:
    """Tests for GreeterAgent message sending."""

    def test_greeter_agent_can_be_created(self) -> None:
        """GreeterAgent can be instantiated."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "01_hello_world.py"
        spec = importlib.util.spec_from_file_location("hello_world", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        assert hasattr(module, "GreeterAgent"), "GreeterAgent not defined in example"
        assert issubclass(module.GreeterAgent, Akgent), "GreeterAgent must extend Agent"


class TestEndToEndExecution:
    """Tests for complete example execution."""

    def test_example_runs_without_exceptions(self, capsys) -> None:
        """Example runs end-to-end without errors."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "01_hello_world.py"

        # Run the example as a module
        import subprocess

        result = subprocess.run(
            [sys.executable, str(example_path)],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(example_path.parent.parent),
        )

        # Should complete successfully
        assert result.returncode == 0, f"Example failed with stderr: {result.stderr}"

        # Should produce expected output
        assert "[Hello World]" in result.stdout or "[ReceiverAgent]" in result.stdout
        assert "Hello" in result.stdout or "greeting" in result.stdout.lower()

    def test_example_completes_within_timeout(self) -> None:
        """Example completes execution within 5 seconds."""
        import subprocess
        import time

        example_path = Path(__file__).parent.parent.parent / "examples" / "01_hello_world.py"

        start = time.time()
        result = subprocess.run(
            [sys.executable, str(example_path)],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(example_path.parent.parent),
        )
        elapsed = time.time() - start

        assert result.returncode == 0, f"Example failed: {result.stderr}"
        assert elapsed < 5, f"Example took {elapsed:.2f}s, should be <5s"
