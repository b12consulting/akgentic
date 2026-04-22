"""Tests for pricing table, aggregation models, and aggregate_usage()."""

from __future__ import annotations

import dataclasses

from pydantic import BaseModel

from akgentic.llm.event import LlmUsageEvent
from akgentic.llm.pricing import (
    PRICING,
    AgentUsageSummary,
    ModelUsage,
    RunUsageSummary,
    aggregate_usage,
)


def _make_event(
    run_id: str = "run-1",
    model_name: str = "claude-sonnet-4-20250514",
    provider_name: str = "anthropic",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    requests: int = 1,
) -> LlmUsageEvent:
    return LlmUsageEvent(
        run_id=run_id,
        model_name=model_name,
        provider_name=provider_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        requests=requests,
    )


class TestModelUsage:
    """AC-2: ModelUsage is a frozen dataclass with correct fields."""

    def test_is_frozen_dataclass(self) -> None:
        assert dataclasses.is_dataclass(ModelUsage)
        usage = ModelUsage(
            model_name="m",
            provider_name="p",
            input_tokens=1,
            output_tokens=2,
            cache_read_tokens=3,
            cache_write_tokens=4,
            requests=5,
            estimated_cost_usd=0.1,
        )
        assert dataclasses.fields(usage) is not None
        # Frozen check
        try:
            usage.input_tokens = 999  # type: ignore[misc]
            raise AssertionError("Should be frozen")
        except dataclasses.FrozenInstanceError:
            pass

    def test_has_correct_fields(self) -> None:
        field_names = {f.name for f in dataclasses.fields(ModelUsage)}
        expected = {
            "model_name",
            "provider_name",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "requests",
            "estimated_cost_usd",
        }
        assert field_names == expected


class TestRunUsageSummary:
    """AC-3: RunUsageSummary is a frozen dataclass with correct fields."""

    def test_is_frozen_dataclass(self) -> None:
        assert dataclasses.is_dataclass(RunUsageSummary)
        summary = RunUsageSummary(
            run_id="r",
            models=[],
            total_input_tokens=0,
            total_output_tokens=0,
            total_cost_usd=0.0,
        )
        try:
            summary.run_id = "x"  # type: ignore[misc]
            raise AssertionError("Should be frozen")
        except dataclasses.FrozenInstanceError:
            pass

    def test_has_correct_fields(self) -> None:
        field_names = {f.name for f in dataclasses.fields(RunUsageSummary)}
        expected = {
            "run_id",
            "models",
            "total_input_tokens",
            "total_output_tokens",
            "total_cost_usd",
        }
        assert field_names == expected


class TestAgentUsageSummary:
    """AC-4: AgentUsageSummary is a Pydantic BaseModel."""

    def test_is_pydantic_basemodel(self) -> None:
        assert issubclass(AgentUsageSummary, BaseModel)

    def test_defaults(self) -> None:
        s = AgentUsageSummary()
        assert s.by_model == {}
        assert s.runs == []
        assert s.total_input_tokens == 0
        assert s.total_output_tokens == 0
        assert s.total_cache_read_tokens == 0
        assert s.total_cache_write_tokens == 0
        assert s.total_requests == 0
        assert s.total_cost_usd == 0.0

    def test_serialization_roundtrip(self) -> None:
        events = [
            _make_event(input_tokens=1000, output_tokens=500, cache_read_tokens=100),
        ]
        summary = aggregate_usage(events)
        data = summary.model_dump()
        restored = AgentUsageSummary.model_validate(data)
        assert restored.total_cost_usd == summary.total_cost_usd
        assert restored.total_input_tokens == summary.total_input_tokens
        assert restored.by_model == summary.by_model


class TestPricingTable:
    """AC-1: PRICING contains required model entries."""

    def test_required_models_present(self) -> None:
        required = [
            "claude-sonnet-4-20250514",
            "claude-opus-4-20250514",
            "gpt-4o",
            "gpt-5.2",
        ]
        for model in required:
            assert model in PRICING, f"{model} missing from PRICING"

    def test_required_keys(self) -> None:
        required_keys = {"input", "output", "cache_read", "cache_write"}
        for model, rates in PRICING.items():
            assert set(rates.keys()) == required_keys, (
                f"{model} has wrong keys: {set(rates.keys())}"
            )

    def test_values_are_floats(self) -> None:
        for model, rates in PRICING.items():
            for key, val in rates.items():
                assert isinstance(val, (int, float)), f"{model}.{key} is not numeric"


class TestAggregateUsageEmpty:
    """AC-8: Empty event list returns zeroed summary."""

    def test_empty_list(self) -> None:
        result = aggregate_usage([])
        assert result.total_input_tokens == 0
        assert result.total_output_tokens == 0
        assert result.total_cache_read_tokens == 0
        assert result.total_cache_write_tokens == 0
        assert result.total_requests == 0
        assert result.total_cost_usd == 0.0
        assert result.by_model == {}
        assert result.runs == []


class TestAggregateUsageSingleModel:
    """AC-5: Single-model aggregation with correct totals and cost."""

    def test_single_event(self) -> None:
        events = [
            _make_event(
                input_tokens=1_000_000,
                output_tokens=500_000,
            ),
        ]
        result = aggregate_usage(events)
        assert result.total_input_tokens == 1_000_000
        assert result.total_output_tokens == 500_000
        assert result.total_requests == 1
        assert len(result.by_model) == 1
        model = result.by_model["claude-sonnet-4-20250514"]
        assert model.input_tokens == 1_000_000
        assert model.output_tokens == 500_000
        expected_cost = (1_000_000 * 3.0 + 500_000 * 15.0) / 1_000_000
        assert model.estimated_cost_usd == expected_cost
        assert result.runs == []

    def test_multiple_events_same_model(self) -> None:
        events = [
            _make_event(input_tokens=100, output_tokens=50, requests=1),
            _make_event(input_tokens=200, output_tokens=100, requests=1),
        ]
        result = aggregate_usage(events)
        assert result.total_input_tokens == 300
        assert result.total_output_tokens == 150
        assert result.total_requests == 2
        assert len(result.by_model) == 1


class TestAggregateUsageMultiModel:
    """AC-5: Multi-model aggregation with by_model containing one entry per model."""

    def test_two_models(self) -> None:
        events = [
            _make_event(
                model_name="claude-sonnet-4-20250514",
                provider_name="anthropic",
                input_tokens=1000,
                output_tokens=500,
            ),
            _make_event(
                model_name="gpt-4o",
                provider_name="openai",
                input_tokens=2000,
                output_tokens=1000,
            ),
        ]
        result = aggregate_usage(events)
        assert len(result.by_model) == 2
        assert "claude-sonnet-4-20250514" in result.by_model
        assert "gpt-4o" in result.by_model
        assert result.total_input_tokens == 3000
        assert result.total_output_tokens == 1500

        sonnet = result.by_model["claude-sonnet-4-20250514"]
        assert sonnet.provider_name == "anthropic"
        assert sonnet.input_tokens == 1000

        gpt = result.by_model["gpt-4o"]
        assert gpt.provider_name == "openai"
        assert gpt.input_tokens == 2000


class TestAggregateUsageByRun:
    """AC-6: by_run=True produces RunUsageSummary per run_id."""

    def test_by_run(self) -> None:
        events = [
            _make_event(run_id="run-1", input_tokens=100, output_tokens=50),
            _make_event(run_id="run-1", input_tokens=200, output_tokens=100),
            _make_event(run_id="run-2", input_tokens=300, output_tokens=150),
        ]
        result = aggregate_usage(events, by_run=True)
        assert len(result.runs) == 2
        run_ids = {r.run_id for r in result.runs}
        assert run_ids == {"run-1", "run-2"}

        run1 = next(r for r in result.runs if r.run_id == "run-1")
        assert run1.total_input_tokens == 300
        assert run1.total_output_tokens == 150
        assert len(run1.models) == 1

        run2 = next(r for r in result.runs if r.run_id == "run-2")
        assert run2.total_input_tokens == 300
        assert run2.total_output_tokens == 150

    def test_by_run_cost_consistency(self) -> None:
        """RunUsageSummary.total_cost_usd must equal sum of its models' costs."""
        events = [
            _make_event(
                run_id="run-1",
                model_name="claude-sonnet-4-20250514",
                input_tokens=1000,
                output_tokens=500,
            ),
            _make_event(
                run_id="run-1",
                model_name="gpt-4o",
                provider_name="openai",
                input_tokens=2000,
                output_tokens=1000,
            ),
        ]
        result = aggregate_usage(events, by_run=True)
        assert len(result.runs) == 1
        run = result.runs[0]
        expected_cost = sum(m.estimated_cost_usd for m in run.models)
        assert run.total_cost_usd == expected_cost

    def test_by_run_false_no_runs(self) -> None:
        events = [_make_event()]
        result = aggregate_usage(events, by_run=False)
        assert result.runs == []

    def test_by_run_multi_model_per_run(self) -> None:
        events = [
            _make_event(run_id="run-1", model_name="claude-sonnet-4-20250514", input_tokens=100),
            _make_event(run_id="run-1", model_name="gpt-4o", provider_name="openai",
                        input_tokens=200),
        ]
        result = aggregate_usage(events, by_run=True)
        assert len(result.runs) == 1
        run1 = result.runs[0]
        assert len(run1.models) == 2
        assert run1.total_input_tokens == 300


class TestUnknownModel:
    """AC-7: Unknown model produces estimated_cost_usd == 0.0."""

    def test_unknown_model_cost_zero(self) -> None:
        events = [
            _make_event(
                model_name="unknown-model-xyz",
                input_tokens=5000,
                output_tokens=2000,
            ),
        ]
        result = aggregate_usage(events)
        model = result.by_model["unknown-model-xyz"]
        assert model.estimated_cost_usd == 0.0
        assert model.input_tokens == 5000
        assert model.output_tokens == 2000

    def test_mixed_known_and_unknown(self) -> None:
        events = [
            _make_event(model_name="claude-sonnet-4-20250514", input_tokens=1000),
            _make_event(model_name="unknown-model", input_tokens=500),
        ]
        result = aggregate_usage(events)
        assert result.by_model["unknown-model"].estimated_cost_usd == 0.0
        assert result.by_model["claude-sonnet-4-20250514"].estimated_cost_usd > 0.0


class TestCacheTokenPricing:
    """Cache token pricing included in cost calculation."""

    def test_cache_tokens_affect_cost(self) -> None:
        events = [
            _make_event(
                model_name="claude-sonnet-4-20250514",
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=1_000_000,
                cache_write_tokens=1_000_000,
            ),
        ]
        result = aggregate_usage(events)
        model = result.by_model["claude-sonnet-4-20250514"]
        expected_cost = (1_000_000 * 0.30 + 1_000_000 * 3.75) / 1_000_000
        assert model.estimated_cost_usd == expected_cost
        assert result.total_cache_read_tokens == 1_000_000
        assert result.total_cache_write_tokens == 1_000_000


class TestTotalRequestsAccumulation:
    """total_requests accumulates correctly across multiple events."""

    def test_total_requests_multi_event(self) -> None:
        events = [
            _make_event(requests=3),
            _make_event(requests=2),
            _make_event(model_name="gpt-4o", provider_name="openai", requests=5),
        ]
        result = aggregate_usage(events)
        assert result.total_requests == 10
        assert result.by_model["claude-sonnet-4-20250514"].requests == 5
        assert result.by_model["gpt-4o"].requests == 5


class TestTotalCostConsistency:
    """total_cost_usd equals sum of all model costs."""

    def test_total_equals_sum(self) -> None:
        events = [
            _make_event(
                model_name="claude-sonnet-4-20250514",
                input_tokens=1000,
                output_tokens=500,
            ),
            _make_event(
                model_name="gpt-4o",
                provider_name="openai",
                input_tokens=2000,
                output_tokens=1000,
            ),
            _make_event(
                model_name="unknown-model",
                input_tokens=500,
                output_tokens=250,
            ),
        ]
        result = aggregate_usage(events)
        expected_total = sum(m.estimated_cost_usd for m in result.by_model.values())
        assert result.total_cost_usd == expected_total


class TestPublicApiExport:
    """AC-9: All five exports importable from akgentic.llm and present in __all__."""

    def test_all_exports_importable(self) -> None:
        import akgentic.llm

        assert hasattr(akgentic.llm, "PRICING")
        assert hasattr(akgentic.llm, "AgentUsageSummary")
        assert hasattr(akgentic.llm, "ModelUsage")
        assert hasattr(akgentic.llm, "RunUsageSummary")
        assert hasattr(akgentic.llm, "aggregate_usage")

    def test_all_in_dunder_all(self) -> None:
        import akgentic.llm

        names = ["PRICING", "AgentUsageSummary", "ModelUsage", "RunUsageSummary", "aggregate_usage"]
        for name in names:
            assert name in akgentic.llm.__all__, f"{name} missing from __all__"
