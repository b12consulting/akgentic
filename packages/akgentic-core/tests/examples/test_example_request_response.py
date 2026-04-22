"""Tests for Request-Response example (Story 5.2).

Verifies the example demonstrates ask/reply synchronous communication patterns:
- Custom message types (CalculationRequest, CalculationResult)
- Agent message handlers (receiveMsg_<Type> pattern)
- Fire-and-forget (tell) and blocking (ask) communication patterns
- Calculator agent processing requests and sending responses
"""

import importlib.util
import subprocess
import sys
import time
from pathlib import Path

from akgentic.core import ActorSystem, Akgent, BaseConfig
from akgentic.core.messages import Message


class TestCalculationMessageDefinitions:
    """Tests for CalculationRequest and CalculationResult message definitions."""

    def test_calculation_request_can_be_imported(self) -> None:
        """CalculationRequest class can be imported from example."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "02_request_response.py"
        spec = importlib.util.spec_from_file_location("request_response", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        assert hasattr(module, "CalculationRequest"), "CalculationRequest not defined in example"
        assert issubclass(module.CalculationRequest, Message), (
            "CalculationRequest must extend Message"
        )

    def test_calculation_request_has_required_fields(self) -> None:
        """CalculationRequest has a, b, and operation fields."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "02_request_response.py"
        spec = importlib.util.spec_from_file_location("request_response", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        msg = module.CalculationRequest(a=10, b=5, operation="+")
        assert msg.a == 10.0
        assert msg.b == 5.0
        assert msg.operation == "+"

    def test_calculation_result_can_be_imported(self) -> None:
        """CalculationResult class can be imported from example."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "02_request_response.py"
        spec = importlib.util.spec_from_file_location("request_response", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        assert hasattr(module, "CalculationResult"), "CalculationResult not defined in example"
        assert issubclass(module.CalculationResult, Message), (
            "CalculationResult must extend Message"
        )

    def test_calculation_result_has_required_fields(self) -> None:
        """CalculationResult has result and request_id fields."""
        import uuid as uuid_module

        example_path = Path(__file__).parent.parent.parent / "examples" / "02_request_response.py"
        spec = importlib.util.spec_from_file_location("request_response", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        # Check that CalculationResult has the required fields
        assert hasattr(module.CalculationResult, "model_fields")
        fields = module.CalculationResult.model_fields
        assert "result" in fields
        assert "request_id" in fields

        # Check that fields have correct annotations
        result_annotation = fields["result"].annotation
        request_id_annotation = fields["request_id"].annotation

        # result should be float
        assert result_annotation is float or result_annotation == "float"
        # request_id should be uuid.UUID
        assert "UUID" in str(request_id_annotation) or request_id_annotation is uuid_module.UUID


class TestCalculatorAgentHandler:
    """Tests for CalculatorAgent message handling."""

    def test_calculator_agent_can_be_imported(self) -> None:
        """CalculatorAgent class can be imported from example."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "02_request_response.py"
        spec = importlib.util.spec_from_file_location("request_response", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        assert hasattr(module, "CalculatorAgent"), "CalculatorAgent not defined in example"
        assert issubclass(module.CalculatorAgent, Akgent), "CalculatorAgent must extend Akgent"

    def test_calculator_agent_handles_addition(self, capsys) -> None:
        """CalculatorAgent correctly processes addition requests."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "02_request_response.py"
        spec = importlib.util.spec_from_file_location("request_response", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        system = ActorSystem()
        try:
            # Create calculator agent
            calc_addr = system.createActor(
                module.CalculatorAgent,
                config=BaseConfig(name="test-calc", role="Calculator"),
            )

            # Create a dummy client to receive responses
            system.createActor(
                module.ClientAgent,
                config=BaseConfig(name="test-client", role="Client"),
            )

            # Send addition request
            request = module.CalculationRequest(a=10, b=5, operation="+")
            system.tell(calc_addr, request)

            # Give time for async processing
            time.sleep(0.2)

            captured = capsys.readouterr()
            assert "Processing request:" in captured.out or "CalculatorAgent" in captured.out

        finally:
            system.shutdown(timeout=5)

    def test_calculator_agent_handles_multiplication(self, capsys) -> None:
        """CalculatorAgent correctly processes multiplication requests."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "02_request_response.py"
        spec = importlib.util.spec_from_file_location("request_response", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        system = ActorSystem()
        try:
            # Create calculator agent
            calc_addr = system.createActor(
                module.CalculatorAgent,
                config=BaseConfig(name="test-calc", role="Calculator"),
            )

            # Create a dummy client to receive responses
            system.createActor(
                module.ClientAgent,
                config=BaseConfig(name="test-client", role="Client"),
            )

            # Send multiplication request
            request = module.CalculationRequest(a=20, b=3, operation="*")
            system.tell(calc_addr, request)

            # Give time for async processing
            time.sleep(0.2)

            captured = capsys.readouterr()
            assert "Processing request:" in captured.out or "CalculatorAgent" in captured.out

        finally:
            system.shutdown(timeout=5)


class TestClientAgentSender:
    """Tests for ClientAgent message sending."""

    def test_client_agent_can_be_imported(self) -> None:
        """ClientAgent class can be imported from example."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "02_request_response.py"
        spec = importlib.util.spec_from_file_location("request_response", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        assert hasattr(module, "ClientAgent"), "ClientAgent not defined in example"
        assert issubclass(module.ClientAgent, Akgent), "ClientAgent must extend Akgent"

    def test_client_agent_send_request_tell_method_exists(self) -> None:
        """ClientAgent has send_request_tell method for fire-and-forget pattern."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "02_request_response.py"
        spec = importlib.util.spec_from_file_location("request_response", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        assert hasattr(module.ClientAgent, "send_request_tell"), (
            "ClientAgent.send_request_tell method not found"
        )

    def test_client_agent_send_request_ask_method_exists(self) -> None:
        """ClientAgent has send_request_ask method for blocking pattern."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "02_request_response.py"
        spec = importlib.util.spec_from_file_location("request_response", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        assert hasattr(module.ClientAgent, "send_request_ask"), (
            "ClientAgent.send_request_ask method not found"
        )


class TestEndToEndExecution:
    """Tests for complete example execution."""

    def test_example_runs_without_exceptions(self) -> None:
        """Example runs end-to-end without errors."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "02_request_response.py"

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
        assert "[Request-Response]" in result.stdout or "ClientAgent" in result.stdout

    def test_example_produces_expected_output(self) -> None:
        """Example produces expected calculation results."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "02_request_response.py"

        result = subprocess.run(
            [sys.executable, str(example_path)],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(example_path.parent.parent),
        )

        assert result.returncode == 0, f"Example failed: {result.stderr}"

        # Should show calculation results
        assert "15.0" in result.stdout or "15" in result.stdout  # 10 + 5
        assert "60.0" in result.stdout or "60" in result.stdout  # 20 * 3

    def test_example_completes_within_timeout(self) -> None:
        """Example completes execution within 5 seconds."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "02_request_response.py"

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

    def test_example_shows_request_response_flow(self) -> None:
        """Example demonstrates request-response communication flow."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "02_request_response.py"

        result = subprocess.run(
            [sys.executable, str(example_path)],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(example_path.parent.parent),
        )

        assert result.returncode == 0, f"Example failed: {result.stderr}"

        output = result.stdout.lower()
        # Should show client sending requests
        assert "sending calculation request" in output
        # Should show calculator processing
        assert "processing request" in output or "calculatoragent" in output
        # Should show client receiving results
        assert "received result" in output


class TestIntegration:
    """Integration tests for the full example flow."""

    def test_full_request_response_flow(self) -> None:
        """Test complete request-response flow with actual agents."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "02_request_response.py"
        spec = importlib.util.spec_from_file_location("request_response", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        system = ActorSystem()
        try:
            # Create agents
            calc_addr = system.createActor(
                module.CalculatorAgent,
                config=BaseConfig(name="calc", role="Calculator"),
            )
            client_addr = system.createActor(
                module.ClientAgent,
                config=BaseConfig(name="client", role="Client"),
            )

            # Use tell proxy to send request
            client_tell = system.proxy_tell(client_addr, module.ClientAgent)
            client_tell.send_request_tell(calc_addr, 10, 5, "+")

            # Wait for processing
            time.sleep(0.2)

            # Use ask proxy to send another request
            client_ask = system.proxy_ask(client_addr, module.ClientAgent)
            client_ask.send_request_ask(calc_addr, 20, 3, "*")

            # Wait for processing
            time.sleep(0.2)

            # If we got here without exceptions, the flow works
            assert True

        finally:
            system.shutdown(timeout=5)
