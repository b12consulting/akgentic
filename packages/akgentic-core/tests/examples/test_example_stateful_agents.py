"""Tests for Stateful Agents example (Story 5.4 + Story 5.8).

Verifies the example demonstrates state management and typed configuration patterns:
- CounterConfig extending BaseConfig with max_increment and label_prefix (Story 5.8)
- Custom state types (CounterState) with observer pattern
- State initialization driven by self.config inside on_start()
- Explicit state mutation with notification (clamped via self.config.max_increment)
- History labels prefixed via self.config.label_prefix
- Orchestrator tracking of state changes
- Querying final state via Orchestrator.get_states()
"""

import importlib.util
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from akgentic.core import ActorSystem, BaseConfig, BaseState, Orchestrator
from akgentic.core.messages import Message

EXAMPLE_PATH = Path(__file__).parent.parent.parent / "examples" / "04_stateful_agents.py"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("stateful_agents", EXAMPLE_PATH)
    assert spec is not None, f"Example file not found: {EXAMPLE_PATH}"
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None, "No loader for example module"
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# =============================================================================
# Story 5.8 — CounterConfig
# =============================================================================


class TestCounterConfigDefinition:
    """Tests for CounterConfig class definition (AC 1)."""

    def test_counter_config_can_be_imported(self) -> None:
        """CounterConfig class is defined in the example."""
        module = _load_module()
        assert hasattr(module, "CounterConfig"), "CounterConfig not defined in example"

    def test_counter_config_extends_base_config(self) -> None:
        """CounterConfig extends BaseConfig (AC 1)."""
        module = _load_module()
        assert issubclass(module.CounterConfig, BaseConfig), (  # type: ignore[attr-defined]
            "CounterConfig must extend BaseConfig"
        )

    def test_counter_config_has_max_increment(self) -> None:
        """CounterConfig has max_increment field defaulting to 10 (AC 1)."""
        module = _load_module()
        cfg = module.CounterConfig()  # type: ignore[attr-defined]
        assert hasattr(cfg, "max_increment"), "CounterConfig missing max_increment"
        assert cfg.max_increment == 10, (
            f"Expected default max_increment=10, got {cfg.max_increment}"
        )

    def test_counter_config_has_label_prefix(self) -> None:
        """CounterConfig has label_prefix field defaulting to '' (AC 1)."""
        module = _load_module()
        cfg = module.CounterConfig()  # type: ignore[attr-defined]
        assert hasattr(cfg, "label_prefix"), "CounterConfig missing label_prefix"
        assert cfg.label_prefix == "", f"Expected default label_prefix='', got {cfg.label_prefix!r}"

    def test_counter_config_custom_values(self) -> None:
        """CounterConfig accepts custom max_increment and label_prefix values (AC 1)."""
        module = _load_module()
        cfg = module.CounterConfig(  # type: ignore[attr-defined]
            name="counter",
            role="Counter",
            max_increment=5,
            label_prefix="DEMO",
        )
        assert cfg.max_increment == 5
        assert cfg.label_prefix == "DEMO"


class TestCounterAgentTypedWithCounterConfig:
    """Tests for CounterAgent declared as Akgent[CounterConfig, CounterState] (AC 2)."""

    def test_counter_agent_uses_counter_config(self) -> None:
        """CounterAgent can be created with CounterConfig (AC 2)."""
        module = _load_module()
        system = ActorSystem()
        try:
            addr = system.createActor(
                module.CounterAgent,  # type: ignore[attr-defined]
                config=module.CounterConfig(name="test", role="Counter"),  # type: ignore[attr-defined]
            )
            time.sleep(0.1)
            assert addr is not None
            assert addr.is_alive()
        finally:
            system.shutdown(timeout=5)


class TestInitReadsConfig:
    """Tests that on_start() reads self.config (AC 3)."""

    def test_init_sets_last_operation_with_prefix(self) -> None:
        """on_start() sets last_operation using label_prefix when non-empty (AC 3)."""
        module = _load_module()
        system = ActorSystem()
        try:
            orchestrator_addr = system.createActor(
                Orchestrator,
                config=BaseConfig(name="orchestrator", role="Orchestrator"),
            )
            orch_proxy = system.proxy_ask(orchestrator_addr, Orchestrator)
            orch_proxy.createActor(
                module.CounterAgent,  # type: ignore[attr-defined]
                config=module.CounterConfig(  # type: ignore[attr-defined]
                    name="counter", role="Counter", max_increment=5, label_prefix="DEMO"
                ),
            )
            time.sleep(0.2)
            states = orch_proxy.get_states()
            assert len(states) > 0, "Observer should track state during init"
            final_state = next(iter(states.values()))
            assert isinstance(final_state, module.CounterState)  # type: ignore[attr-defined]
            assert "[DEMO]" in final_state.last_operation, (
                f"last_operation should include prefix, got: {final_state.last_operation!r}"
            )
        finally:
            system.shutdown(timeout=5)

    def test_init_sets_last_operation_without_prefix(self) -> None:
        """on_start() sets last_operation without brackets when label_prefix is empty (AC 3)."""
        module = _load_module()
        system = ActorSystem()
        try:
            orchestrator_addr = system.createActor(
                Orchestrator,
                config=BaseConfig(name="orchestrator", role="Orchestrator"),
            )
            orch_proxy = system.proxy_ask(orchestrator_addr, Orchestrator)
            orch_proxy.createActor(
                module.CounterAgent,  # type: ignore[attr-defined]
                config=module.CounterConfig(name="counter", role="Counter"),  # type: ignore[attr-defined]
            )
            time.sleep(0.2)
            states = orch_proxy.get_states()
            final_state = next(iter(states.values()))
            assert isinstance(final_state, module.CounterState)  # type: ignore[attr-defined]
            assert final_state.last_operation == "Agent ready", (
                f"Expected 'Agent ready', got: {final_state.last_operation!r}"
            )
        finally:
            system.shutdown(timeout=5)

    def test_observer_still_attached_after_on_start(self) -> None:
        """Observer is still attached after on_start() reads config (AC 3)."""
        module = _load_module()
        system = ActorSystem()
        try:
            orchestrator_addr = system.createActor(
                Orchestrator,
                config=BaseConfig(name="orchestrator", role="Orchestrator"),
            )
            orch_proxy = system.proxy_ask(orchestrator_addr, Orchestrator)
            counter_addr = orch_proxy.createActor(
                module.CounterAgent,  # type: ignore[attr-defined]
                config=module.CounterConfig(name="counter", role="Counter", label_prefix="TEST"),  # type: ignore[attr-defined]
            )
            time.sleep(0.1)
            system.tell(counter_addr, module.IncrementMessage(amount=1, label="probe"))  # type: ignore[attr-defined]
            time.sleep(0.2)
            states = orch_proxy.get_states()
            assert len(states) > 0, "Observer should report state after increment"
        finally:
            system.shutdown(timeout=5)


class TestMaxIncrementClamping:
    """Tests for clamping via self.config.max_increment (AC 4)."""

    def test_increment_within_limit_not_clamped(self, capsys) -> None:  # type: ignore[no-untyped-def]
        """Increment at or below max_increment is applied as-is (AC 4)."""
        module = _load_module()
        system = ActorSystem()
        try:
            orchestrator_addr = system.createActor(
                Orchestrator,
                config=BaseConfig(name="orchestrator", role="Orchestrator"),
            )
            orch_proxy = system.proxy_ask(orchestrator_addr, Orchestrator)
            counter_addr = orch_proxy.createActor(
                module.CounterAgent,  # type: ignore[attr-defined]
                config=module.CounterConfig(name="counter", role="Counter", max_increment=5),  # type: ignore[attr-defined]
            )
            system.tell(counter_addr, module.IncrementMessage(amount=5, label="exact"))  # type: ignore[attr-defined]
            time.sleep(0.2)
            captured = capsys.readouterr()
            assert "requested=5 → effective=5" in captured.out
            states = orch_proxy.get_states()
            final = next(iter(states.values()))
            assert isinstance(final, module.CounterState)  # type: ignore[attr-defined]
            assert final.count == 5
        finally:
            system.shutdown(timeout=5)

    def test_increment_above_limit_is_clamped(self, capsys) -> None:  # type: ignore[no-untyped-def]
        """Increment above max_increment is clamped to max_increment (AC 4)."""
        module = _load_module()
        system = ActorSystem()
        try:
            orchestrator_addr = system.createActor(
                Orchestrator,
                config=BaseConfig(name="orchestrator", role="Orchestrator"),
            )
            orch_proxy = system.proxy_ask(orchestrator_addr, Orchestrator)
            counter_addr = orch_proxy.createActor(
                module.CounterAgent,  # type: ignore[attr-defined]
                config=module.CounterConfig(name="counter", role="Counter", max_increment=5),  # type: ignore[attr-defined]
            )
            system.tell(counter_addr, module.IncrementMessage(amount=10, label="over-limit"))  # type: ignore[attr-defined]
            time.sleep(0.2)
            captured = capsys.readouterr()
            assert "requested=10 → effective=5" in captured.out, (
                f"Should show clamping. Got: {captured.out!r}"
            )
            states = orch_proxy.get_states()
            final = next(iter(states.values()))
            assert isinstance(final, module.CounterState)  # type: ignore[attr-defined]
            assert final.count == 5, f"Expected clamped count=5, got {final.count}"
        finally:
            system.shutdown(timeout=5)


class TestLabelPrefixing:
    """Tests for history label prefixing via self.config.label_prefix (AC 5)."""

    def test_increment_label_prefixed_when_prefix_set(self, capsys) -> None:  # type: ignore[no-untyped-def]
        """Increment history label is prefixed with label_prefix (AC 5)."""
        module = _load_module()
        system = ActorSystem()
        try:
            orchestrator_addr = system.createActor(
                Orchestrator,
                config=BaseConfig(name="orchestrator", role="Orchestrator"),
            )
            orch_proxy = system.proxy_ask(orchestrator_addr, Orchestrator)
            counter_addr = orch_proxy.createActor(
                module.CounterAgent,  # type: ignore[attr-defined]
                config=module.CounterConfig(  # type: ignore[attr-defined]
                    name="counter", role="Counter", max_increment=10, label_prefix="DEMO"
                ),
            )
            system.tell(counter_addr, module.IncrementMessage(amount=3, label="my-op"))  # type: ignore[attr-defined]
            time.sleep(0.2)
            captured = capsys.readouterr()
            assert "[DEMO] my-op" in captured.out
            states = orch_proxy.get_states()
            final = next(iter(states.values()))
            assert isinstance(final, module.CounterState)  # type: ignore[attr-defined]
            assert "[DEMO] my-op" in final.history
        finally:
            system.shutdown(timeout=5)

    def test_reset_label_prefixed_when_prefix_set(self, capsys) -> None:  # type: ignore[no-untyped-def]
        """Reset history label is prefixed with label_prefix (AC 5)."""
        module = _load_module()
        system = ActorSystem()
        try:
            orchestrator_addr = system.createActor(
                Orchestrator,
                config=BaseConfig(name="orchestrator", role="Orchestrator"),
            )
            orch_proxy = system.proxy_ask(orchestrator_addr, Orchestrator)
            counter_addr = orch_proxy.createActor(
                module.CounterAgent,  # type: ignore[attr-defined]
                config=module.CounterConfig(  # type: ignore[attr-defined]
                    name="counter", role="Counter", label_prefix="DEMO"
                ),
            )
            system.tell(counter_addr, module.ResetMessage(reason="my-reason"))  # type: ignore[attr-defined]
            time.sleep(0.2)
            states = orch_proxy.get_states()
            final = next(iter(states.values()))
            assert isinstance(final, module.CounterState)  # type: ignore[attr-defined]
            assert "[DEMO] my-reason" in final.history
        finally:
            system.shutdown(timeout=5)

    def test_no_prefix_when_label_prefix_empty(self, capsys) -> None:  # type: ignore[no-untyped-def]
        """Labels are not prefixed when label_prefix is empty (AC 5)."""
        module = _load_module()
        system = ActorSystem()
        try:
            orchestrator_addr = system.createActor(
                Orchestrator,
                config=BaseConfig(name="orchestrator", role="Orchestrator"),
            )
            orch_proxy = system.proxy_ask(orchestrator_addr, Orchestrator)
            counter_addr = orch_proxy.createActor(
                module.CounterAgent,  # type: ignore[attr-defined]
                config=module.CounterConfig(name="counter", role="Counter"),  # type: ignore[attr-defined]
            )
            system.tell(counter_addr, module.IncrementMessage(amount=2, label="plain"))  # type: ignore[attr-defined]
            time.sleep(0.2)
            states = orch_proxy.get_states()
            final = next(iter(states.values()))
            assert isinstance(final, module.CounterState)  # type: ignore[attr-defined]
            assert "plain" in final.history
            assert "[" not in final.history[0], (
                "Should not have bracket prefix when label_prefix=''"
            )
        finally:
            system.shutdown(timeout=5)


# =============================================================================
# Preserved tests — updated for new output format
# =============================================================================


class TestCounterStateDefinition:
    """Tests for CounterState class definition."""

    def test_counter_state_can_be_imported(self) -> None:
        """CounterState class can be imported from example."""
        module = _load_module()
        assert hasattr(module, "CounterState"), "CounterState not defined in example"
        assert issubclass(module.CounterState, BaseState), "CounterState must extend BaseState"  # type: ignore[attr-defined]

    def test_counter_state_has_required_fields(self) -> None:
        """CounterState has count, history, and last_operation fields."""
        module = _load_module()
        state = module.CounterState()  # type: ignore[attr-defined]
        assert hasattr(state, "count"), "CounterState missing 'count' field"
        assert hasattr(state, "history"), "CounterState missing 'history' field"
        assert hasattr(state, "last_operation"), "CounterState missing 'last_operation' field"

    def test_counter_state_initial_values(self) -> None:
        """CounterState initializes with correct default values."""
        module = _load_module()
        state = module.CounterState()  # type: ignore[attr-defined]
        assert state.count == 0, "count should default to 0"
        assert state.history == [], "history should default to empty list"
        assert state.last_operation == "", "last_operation should default to empty string"


class TestCounterMessageTypes:
    """Tests for message types."""

    def test_increment_message_definition(self) -> None:
        """IncrementMessage has amount and label fields."""
        module = _load_module()
        assert hasattr(module, "IncrementMessage"), "IncrementMessage not defined"
        assert issubclass(module.IncrementMessage, Message), "IncrementMessage must extend Message"  # type: ignore[attr-defined]
        msg = module.IncrementMessage(amount=5, label="test")  # type: ignore[attr-defined]
        assert msg.amount == 5
        assert msg.label == "test"

    def test_reset_message_definition(self) -> None:
        """ResetMessage has reason field."""
        module = _load_module()
        assert hasattr(module, "ResetMessage"), "ResetMessage not defined"
        assert issubclass(module.ResetMessage, Message), "ResetMessage must extend Message"  # type: ignore[attr-defined]
        msg = module.ResetMessage(reason="test reset")  # type: ignore[attr-defined]
        assert msg.reason == "test reset"


class TestCounterAgentStateInitialization:
    """Tests for CounterAgent state initialization."""

    def test_counter_agent_initializes_state(self) -> None:
        """CounterAgent.on_start() initializes state."""
        module = _load_module()
        system = ActorSystem()
        try:
            counter_addr = system.createActor(
                module.CounterAgent,  # type: ignore[attr-defined]
                config=module.CounterConfig(name="test-counter", role="Counter"),  # type: ignore[attr-defined]
            )
            time.sleep(0.1)
            assert counter_addr is not None
            assert counter_addr.is_alive()
        finally:
            system.shutdown(timeout=5)

    def test_counter_agent_attaches_observer(self) -> None:
        """CounterAgent attaches itself as observer to state."""
        module = _load_module()
        system = ActorSystem()
        try:
            orchestrator_addr = system.createActor(
                Orchestrator,
                config=BaseConfig(name="orchestrator", role="Orchestrator"),
            )
            orch_proxy = system.proxy_ask(orchestrator_addr, Orchestrator)
            orch_proxy.createActor(
                module.CounterAgent,  # type: ignore[attr-defined]
                config=module.CounterConfig(name="test-counter", role="Counter"),  # type: ignore[attr-defined]
            )
            time.sleep(0.2)
            states = orch_proxy.get_states()
            assert len(states) > 0, "Observer should track state during init"
        finally:
            system.shutdown(timeout=5)


class TestCounterAgentStateMutations:
    """Tests for state mutations through message handlers."""

    def test_increment_message_increments_count(self, capsys) -> None:  # type: ignore[no-untyped-def]
        """IncrementMessage increments count and notifies."""
        module = _load_module()
        system = ActorSystem()
        try:
            orchestrator_addr = system.createActor(
                Orchestrator,
                config=BaseConfig(name="orchestrator", role="Orchestrator"),
            )
            orch_proxy = system.proxy_ask(orchestrator_addr, Orchestrator)
            counter_addr = orch_proxy.createActor(
                module.CounterAgent,  # type: ignore[attr-defined]
                config=module.CounterConfig(name="test-counter", role="Counter", max_increment=10),  # type: ignore[attr-defined]
            )
            system.tell(counter_addr, module.IncrementMessage(amount=5, label="test increment"))  # type: ignore[attr-defined]
            time.sleep(0.2)
            captured = capsys.readouterr()
            assert "effective=5" in captured.out
            assert "count: 5" in captured.out
            states = orch_proxy.get_states()
            assert len(states) > 0, "Orchestrator should track agent state"
            final_state = next(iter(states.values()))
            assert isinstance(final_state, module.CounterState)  # type: ignore[attr-defined]
            assert final_state.count == 5, f"Expected count=5, got {final_state.count}"
        finally:
            system.shutdown(timeout=5)

    def test_reset_message_resets_count(self, capsys) -> None:  # type: ignore[no-untyped-def]
        """ResetMessage resets count to zero and notifies."""
        module = _load_module()
        system = ActorSystem()
        try:
            orchestrator_addr = system.createActor(
                Orchestrator,
                config=BaseConfig(name="orchestrator", role="Orchestrator"),
            )
            orch_proxy = system.proxy_ask(orchestrator_addr, Orchestrator)
            counter_addr = orch_proxy.createActor(
                module.CounterAgent,  # type: ignore[attr-defined]
                config=module.CounterConfig(name="test-counter", role="Counter"),  # type: ignore[attr-defined]
            )
            system.tell(counter_addr, module.IncrementMessage(amount=10, label="increment"))  # type: ignore[attr-defined]
            time.sleep(0.1)
            system.tell(counter_addr, module.ResetMessage(reason="test reset"))  # type: ignore[attr-defined]
            time.sleep(0.2)
            captured = capsys.readouterr()
            assert "Reset" in captured.out
            assert "count: 0" in captured.out
            states = orch_proxy.get_states()
            assert len(states) > 0
            final_state = next(iter(states.values()))
            assert isinstance(final_state, module.CounterState)  # type: ignore[attr-defined]
            assert final_state.count == 0, f"Expected count=0 after reset, got {final_state.count}"
        finally:
            system.shutdown(timeout=5)

    def test_history_tracks_operations(self) -> None:
        """State history field tracks all operations."""
        module = _load_module()
        system = ActorSystem()
        try:
            orchestrator_addr = system.createActor(
                Orchestrator,
                config=BaseConfig(name="orchestrator", role="Orchestrator"),
            )
            orch_proxy = system.proxy_ask(orchestrator_addr, Orchestrator)
            counter_addr = orch_proxy.createActor(
                module.CounterAgent,  # type: ignore[attr-defined]
                config=module.CounterConfig(name="test-counter", role="Counter"),  # type: ignore[attr-defined]
            )
            system.tell(counter_addr, module.IncrementMessage(amount=5, label="op1"))  # type: ignore[attr-defined]
            time.sleep(0.1)
            system.tell(counter_addr, module.IncrementMessage(amount=3, label="op2"))  # type: ignore[attr-defined]
            time.sleep(0.2)
            states = orch_proxy.get_states()
            assert len(states) > 0
            final_state = next(iter(states.values()))
            assert isinstance(final_state, module.CounterState)  # type: ignore[attr-defined]
            # Without prefix, labels appear as-is
            assert any("op1" in h for h in final_state.history)
            assert any("op2" in h for h in final_state.history)
        finally:
            system.shutdown(timeout=5)


class TestOrchestratorStateTracking:
    """Tests for Orchestrator tracking of state changes."""

    def test_orchestrator_tracks_state_changes(self) -> None:
        """Orchestrator.get_states() returns tracked agent states."""
        module = _load_module()
        system = ActorSystem()
        try:
            orchestrator_addr = system.createActor(
                Orchestrator,
                config=BaseConfig(name="orchestrator", role="Orchestrator"),
            )
            orch_proxy = system.proxy_ask(orchestrator_addr, Orchestrator)
            counter_addr = orch_proxy.createActor(
                module.CounterAgent,  # type: ignore[attr-defined]
                config=module.CounterConfig(name="test-counter", role="Counter"),  # type: ignore[attr-defined]
            )
            system.tell(counter_addr, module.IncrementMessage(amount=5, label="test"))  # type: ignore[attr-defined]
            time.sleep(0.2)
            states = orch_proxy.get_states()
            assert isinstance(states, dict), "get_states() should return dict"
            assert len(states) > 0, "Orchestrator should track agent states"
        finally:
            system.shutdown(timeout=5)

    def test_orchestrator_tracks_all_state_mutations(self) -> None:
        """Orchestrator records state mutations through get_states()."""
        module = _load_module()
        system = ActorSystem()
        try:
            orchestrator_addr = system.createActor(
                Orchestrator,
                config=BaseConfig(name="orchestrator", role="Orchestrator"),
            )
            orch_proxy = system.proxy_ask(orchestrator_addr, Orchestrator)
            counter_addr = orch_proxy.createActor(
                module.CounterAgent,  # type: ignore[attr-defined]
                config=module.CounterConfig(name="test-counter", role="Counter", max_increment=10),  # type: ignore[attr-defined]
            )
            system.tell(counter_addr, module.IncrementMessage(amount=5, label="inc1"))  # type: ignore[attr-defined]
            time.sleep(0.1)
            system.tell(counter_addr, module.IncrementMessage(amount=3, label="inc2"))  # type: ignore[attr-defined]
            time.sleep(0.2)
            states = orch_proxy.get_states()
            assert len(states) > 0, "Orchestrator should track agent states"
            final_state = next(iter(states.values()))
            assert isinstance(final_state, module.CounterState)  # type: ignore[attr-defined]
            assert len(final_state.history) == 2, (
                f"Expected 2 mutations, got {len(final_state.history)}"
            )
            assert final_state.count == 8, f"Expected count=8 (5+3), got {final_state.count}"
        finally:
            system.shutdown(timeout=5)


class TestEndToEndExecution:
    """Tests for complete example execution."""

    def test_example_runs_without_exceptions(self) -> None:
        """Example runs end-to-end without errors (AC 10)."""
        result = subprocess.run(
            [sys.executable, str(EXAMPLE_PATH)],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(EXAMPLE_PATH.parent.parent),
        )
        assert result.returncode == 0, f"Example failed with stderr: {result.stderr}"
        assert "[Stateful Agents]" in result.stdout

    def test_example_demonstrates_clamping(self) -> None:
        """Example output shows increment clamping (AC 7, 8)."""
        result = subprocess.run(
            [sys.executable, str(EXAMPLE_PATH)],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(EXAMPLE_PATH.parent.parent),
        )
        assert result.returncode == 0
        assert "requested=10 → effective=5" in result.stdout, (
            f"Should show clamping. Got: {result.stdout}"
        )

    def test_example_demonstrates_prefixed_labels(self) -> None:
        """Example output shows [DEMO] prefixed labels (AC 8)."""
        result = subprocess.run(
            [sys.executable, str(EXAMPLE_PATH)],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(EXAMPLE_PATH.parent.parent),
        )
        assert result.returncode == 0
        assert "[DEMO]" in result.stdout, "Should show DEMO prefix in labels"

    def test_example_tracks_five_state_changes(self) -> None:
        """Example tracks 5 state changes (5 history entries) (AC 8)."""
        result = subprocess.run(
            [sys.executable, str(EXAMPLE_PATH)],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(EXAMPLE_PATH.parent.parent),
        )
        assert result.returncode == 0
        assert "Tracked 5 state changes" in result.stdout, (
            f"Should track 5 changes. Got: {result.stdout}"
        )

    def test_example_final_count_is_five(self) -> None:
        """Example final count is 5 after clamped operations (AC 8)."""
        result = subprocess.run(
            [sys.executable, str(EXAMPLE_PATH)],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(EXAMPLE_PATH.parent.parent),
        )
        assert result.returncode == 0
        assert "count=5" in result.stdout, f"Final count should be 5. Got: {result.stdout}"

    def test_example_completes_within_timeout(self) -> None:
        """Example completes execution within 10 seconds."""
        import time

        start = time.time()
        result = subprocess.run(
            [sys.executable, str(EXAMPLE_PATH)],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(EXAMPLE_PATH.parent.parent),
        )
        elapsed = time.time() - start
        assert result.returncode == 0, f"Example failed: {result.stderr}"
        assert elapsed < 10, f"Example took {elapsed:.2f}s, should be <10s"
