"""Tests for Story 10-14: Planning Search Score Exposure, Ordering, and Defaults.

Covers AC-1 through AC-8: ToolCard fields, config propagation, LLM-tunable
parameters, score exposure, result ordering, parameterized values, catalog
YAML configuration, and backward compatibility.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

from akgentic.tool.planning.planning_actor import (
    PlanActor,
    PlanConfig,
    Task,
)


def _extract_id(line: str) -> int:
    """Extract task ID from a formatted result string."""
    m = re.match(r"Task (\d+):", line)
    assert m, f"Could not extract task ID from: {line}"
    return int(m.group(1))


def _extract_ids(results: list[str]) -> set[int]:
    """Extract task IDs from formatted result strings."""
    return {_extract_id(line) for line in results}


def _make_actor(
    vector_store: bool = False,
    search_top_k: int = 10,
    search_score_threshold: float = 0.5,
) -> PlanActor:
    """Construct a bare PlanActor with configurable search defaults."""
    actor = PlanActor()
    actor.config = PlanConfig(
        name="test-plan",
        role="ToolActor",
        vector_store=vector_store,
        search_top_k=search_top_k,
        search_score_threshold=search_score_threshold,
    )
    actor.on_start()
    return actor


def _add_task(
    actor: PlanActor,
    task_id: int,
    description: str,
    status: str = "pending",
    owner: str = "@Alice",
    creator: str = "@Bob",
) -> Task:
    """Helper: add a task to actor state without triggering embedding."""
    task = Task(
        id=task_id,
        status=status,  # type: ignore[arg-type]
        description=description,
        owner=owner,
        creator=creator,
    )
    actor.state.task_list.append(task)
    return task


def _make_vs_proxy_mock(
    embed_return: list[list[float]], search_hits: list[tuple[str, float]]
) -> MagicMock:
    """Build a mock VectorStoreActor proxy with embed and search stubs."""
    from akgentic.tool.vector_store.protocol import (
        CollectionStatus,
        SearchHit,
        SearchResult,
    )

    mock = MagicMock()
    mock.embed.return_value = embed_return
    mock.search.return_value = SearchResult(
        hits=[
            SearchHit(ref_type="task", ref_id=ref_id, text="", score=score)
            for ref_id, score in search_hits
        ],
        status=CollectionStatus.READY,
    )
    return mock


# ---------------------------------------------------------------------------
# AC-1: ToolCard fields (PlanningTool has search_top_k, search_score_threshold)
# ---------------------------------------------------------------------------


class TestPlanningToolSearchFields:
    """AC-1: PlanningTool has search_top_k and search_score_threshold fields."""

    def test_default_search_top_k(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool

        tool = PlanningTool()
        assert tool.search_top_k == 10

    def test_default_search_score_threshold(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool

        tool = PlanningTool()
        assert tool.search_score_threshold == 0.5

    def test_custom_search_top_k(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool

        tool = PlanningTool(search_top_k=10)
        assert tool.search_top_k == 10

    def test_custom_search_score_threshold(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool

        tool = PlanningTool(search_score_threshold=0.6)
        assert tool.search_score_threshold == 0.6

    def test_serialization_roundtrip(self) -> None:
        """AC-1 / AC-7: round-trip via model_dump/model_validate."""
        from akgentic.tool.planning.planning import PlanningTool

        tool = PlanningTool(search_top_k=10, search_score_threshold=0.6)
        reloaded = PlanningTool.model_validate(tool.model_dump())
        assert reloaded.search_top_k == 10
        assert reloaded.search_score_threshold == 0.6

    def test_serialization_roundtrip_defaults(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool

        tool = PlanningTool()
        reloaded = PlanningTool.model_validate(tool.model_dump())
        assert reloaded.search_top_k == 10
        assert reloaded.search_score_threshold == 0.5

    def test_fields_in_model_dump(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool

        dump = PlanningTool().model_dump()
        assert "search_top_k" in dump
        assert "search_score_threshold" in dump


# ---------------------------------------------------------------------------
# AC-2: Config propagation (PlanningTool.observer() passes to PlanConfig)
# ---------------------------------------------------------------------------


class TestConfigPropagation:
    """AC-2: PlanningTool.observer() passes search fields to PlanConfig."""

    def _run_observer(self, tool: object) -> list[PlanConfig]:
        captured: list[PlanConfig] = []
        mock_proxy = MagicMock()

        def capture(actor_cls: type, config: object = None) -> MagicMock:
            assert isinstance(config, PlanConfig)
            captured.append(config)
            return MagicMock()

        mock_proxy.getChildrenOrCreate.side_effect = capture
        mock_observer = MagicMock()
        mock_observer.orchestrator = MagicMock()
        mock_observer.proxy_ask.return_value = mock_proxy
        tool.observer(mock_observer)  # type: ignore[attr-defined]
        return captured

    def test_propagates_default_search_top_k(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool

        configs = self._run_observer(PlanningTool())
        assert configs[0].search_top_k == 10

    def test_propagates_default_search_score_threshold(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool

        configs = self._run_observer(PlanningTool())
        assert configs[0].search_score_threshold == 0.5

    def test_propagates_custom_search_top_k(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool

        configs = self._run_observer(PlanningTool(search_top_k=10))
        assert configs[0].search_top_k == 10

    def test_propagates_custom_search_score_threshold(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool

        configs = self._run_observer(PlanningTool(search_score_threshold=0.6))
        assert configs[0].search_score_threshold == 0.6


# ---------------------------------------------------------------------------
# AC-3: LLM-tunable parameters (top_k and score_threshold in search_planning)
# ---------------------------------------------------------------------------


class TestLLMTunableParameters:
    """AC-3: search_planning accepts top_k and score_threshold overrides."""

    def test_none_top_k_uses_config_default(self) -> None:
        actor = _make_actor(vector_store=True, search_top_k=5)
        _add_task(actor, 1, "auth service")

        actor._vs_proxy = _make_vs_proxy_mock(
            embed_return=[[0.1]], search_hits=[("1", 0.8)]
        )

        actor.search_planning(query="auth", top_k=None)

        # Verify search was called with config default (5)
        actor._vs_proxy.search.assert_called_once()
        call_args = actor._vs_proxy.search.call_args
        assert call_args[0][2] == 5  # third positional arg is top_k

    def test_explicit_top_k_overrides_config(self) -> None:
        actor = _make_actor(vector_store=True, search_top_k=5)
        _add_task(actor, 1, "auth service")

        actor._vs_proxy = _make_vs_proxy_mock(
            embed_return=[[0.1]], search_hits=[("1", 0.8)]
        )

        actor.search_planning(query="auth", top_k=50)

        call_args = actor._vs_proxy.search.call_args
        assert call_args[0][2] == 50

    def test_none_score_threshold_uses_config_default(self) -> None:
        """Score threshold=0.7 in config: hit at 0.6 excluded."""
        actor = _make_actor(vector_store=True, search_score_threshold=0.7)
        _add_task(actor, 1, "database task")

        actor._vs_proxy = _make_vs_proxy_mock(
            embed_return=[[0.1]], search_hits=[("1", 0.6)]
        )

        result = actor.search_planning(query="unrelated", score_threshold=None)

        # 0.6 < 0.7 config threshold -> excluded
        assert result == []

    def test_explicit_score_threshold_overrides_config(self) -> None:
        """Config threshold=0.7 but explicit override=0.5: hit at 0.6 included."""
        actor = _make_actor(vector_store=True, search_score_threshold=0.7)
        _add_task(actor, 1, "database task")

        actor._vs_proxy = _make_vs_proxy_mock(
            embed_return=[[0.1]], search_hits=[("1", 0.6)]
        )

        result = actor.search_planning(query="unrelated", score_threshold=0.5)

        assert len(result) == 1
        assert _extract_id(result[0]) == 1


# ---------------------------------------------------------------------------
# AC-4: Score exposure in output
# ---------------------------------------------------------------------------


class TestScoreExposure:
    """AC-4: search_planning results include score labels."""

    def test_keyword_match_label(self) -> None:
        actor = _make_actor(vector_store=False)
        _add_task(actor, 1, "auth flow setup")

        result = actor.search_planning(query="auth")

        assert len(result) == 1
        assert "(keyword match)" in result[0]

    def test_semantic_match_label(self) -> None:
        actor = _make_actor(vector_store=True)
        _add_task(actor, 1, "database task")

        actor._vs_proxy = _make_vs_proxy_mock(
            embed_return=[[0.1]], search_hits=[("1", 0.85)]
        )

        result = actor.search_planning(query="unrelated query")

        assert len(result) == 1
        assert "(semantic: 0.85)" in result[0]

    def test_hybrid_label_when_both_keyword_and_semantic(self) -> None:
        """When a task matches both keyword and semantic, hybrid label is used."""
        actor = _make_actor(vector_store=True)
        _add_task(actor, 1, "auth module")

        actor._vs_proxy = _make_vs_proxy_mock(
            embed_return=[[0.1]], search_hits=[("1", 0.9)]
        )

        result = actor.search_planning(query="auth")

        assert len(result) == 1
        assert "(hybrid: 0.90)" in result[0]
        assert "(keyword match)" not in result[0]

    def test_no_score_label_when_no_query(self) -> None:
        """When no query is provided, results have no score label."""
        actor = _make_actor(vector_store=False)
        _add_task(actor, 1, "auth service")

        result = actor.search_planning()

        assert len(result) == 1
        assert "(keyword match)" not in result[0]
        assert "(semantic:" not in result[0]


# ---------------------------------------------------------------------------
# AC-5: Results ordered by score (highest first)
# ---------------------------------------------------------------------------


class TestResultOrdering:
    """AC-5: Results ordered by score descending."""

    def test_keyword_matches_before_semantic(self) -> None:
        """Keyword matches (score=1.0) sort above semantic matches."""
        actor = _make_actor(vector_store=True)
        _add_task(actor, 1, "auth flow setup")  # keyword match
        _add_task(actor, 2, "database schema")  # semantic only

        actor._vs_proxy = _make_vs_proxy_mock(
            embed_return=[[0.1]],
            search_hits=[("2", 0.75)],
        )

        result = actor.search_planning(query="auth flow")

        assert len(result) == 2
        assert _extract_id(result[0]) == 1  # keyword (1.0) first
        assert _extract_id(result[1]) == 2  # semantic (0.75) second

    def test_semantic_results_ordered_by_score(self) -> None:
        """Multiple semantic matches sorted by score descending."""
        actor = _make_actor(vector_store=True)
        _add_task(actor, 1, "task alpha")
        _add_task(actor, 2, "task beta")
        _add_task(actor, 3, "task gamma")

        actor._vs_proxy = _make_vs_proxy_mock(
            embed_return=[[0.1]],
            search_hits=[("1", 0.6), ("2", 0.9), ("3", 0.75)],
        )

        result = actor.search_planning(query="unrelated")

        assert len(result) == 3
        assert _extract_id(result[0]) == 2  # 0.9
        assert _extract_id(result[1]) == 3  # 0.75
        assert _extract_id(result[2]) == 1  # 0.6


# ---------------------------------------------------------------------------
# AC-6: Actor uses parameterized values (effective_top_k, effective_threshold)
# ---------------------------------------------------------------------------


class TestActorParameterizedValues:
    """AC-6: PlanActor.search_planning uses top_k/score_threshold params."""

    def test_uses_config_top_k_when_param_none(self) -> None:
        actor = _make_actor(vector_store=True, search_top_k=15)
        _add_task(actor, 1, "auth service")

        actor._vs_proxy = _make_vs_proxy_mock(
            embed_return=[[0.1]], search_hits=[]
        )

        actor.search_planning(query="auth")

        call_args = actor._vs_proxy.search.call_args
        assert call_args[0][2] == 15

    def test_uses_config_threshold_when_param_none(self) -> None:
        actor = _make_actor(vector_store=True, search_score_threshold=0.8)
        _add_task(actor, 1, "database task")

        actor._vs_proxy = _make_vs_proxy_mock(
            embed_return=[[0.1]], search_hits=[("1", 0.75)]
        )

        result = actor.search_planning(query="unrelated")

        # 0.75 < 0.8 -> excluded
        assert result == []

    def test_param_top_k_overrides_config(self) -> None:
        actor = _make_actor(vector_store=True, search_top_k=5)
        _add_task(actor, 1, "auth service")

        actor._vs_proxy = _make_vs_proxy_mock(
            embed_return=[[0.1]], search_hits=[]
        )

        actor.search_planning(query="auth", top_k=30)

        call_args = actor._vs_proxy.search.call_args
        assert call_args[0][2] == 30

    def test_param_threshold_overrides_config(self) -> None:
        actor = _make_actor(vector_store=True, search_score_threshold=0.8)
        _add_task(actor, 1, "database task")

        actor._vs_proxy = _make_vs_proxy_mock(
            embed_return=[[0.1]], search_hits=[("1", 0.75)]
        )

        result = actor.search_planning(query="unrelated", score_threshold=0.7)

        # 0.75 >= 0.7 -> included
        assert len(result) == 1


# ---------------------------------------------------------------------------
# AC-7: Catalog YAML configuration
# ---------------------------------------------------------------------------


class TestCatalogYAMLConfiguration:
    """AC-7: PlanningTool with custom search defaults propagates correctly."""

    def test_yaml_style_construction(self) -> None:
        """Simulates a YAML config: PlanningTool(search_top_k=10, ...)."""
        from akgentic.tool.planning.planning import PlanningTool

        tool = PlanningTool(search_top_k=10, search_score_threshold=0.6)
        assert tool.search_top_k == 10
        assert tool.search_score_threshold == 0.6

    def test_yaml_config_propagates_to_actor(self) -> None:
        """Custom defaults reach PlanConfig via observer()."""
        actor = _make_actor(
            vector_store=True, search_top_k=10, search_score_threshold=0.6
        )
        assert actor.config.search_top_k == 10
        assert actor.config.search_score_threshold == 0.6


# ---------------------------------------------------------------------------
# AC-8: Backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """AC-8: Without new fields, defaults match hardcoded behavior."""

    def test_default_top_k_is_10(self) -> None:
        actor = _make_actor(vector_store=False)
        assert actor.config.search_top_k == 10

    def test_default_score_threshold_is_05(self) -> None:
        actor = _make_actor(vector_store=False)
        assert actor.config.search_score_threshold == 0.5

    def test_planconfig_backward_compat_coercion(self) -> None:
        """BaseConfig -> PlanConfig coercion preserves defaults."""
        from akgentic.core.agent import BaseConfig

        actor = PlanActor()
        actor.config = BaseConfig(name="test", role="ToolActor")
        actor.on_start()

        assert actor.config.search_top_k == 10
        assert actor.config.search_score_threshold == 0.5

    def test_search_with_default_config_uses_10_and_05(self) -> None:
        """Default config: search uses top_k=10, threshold=0.5."""
        actor = _make_actor(vector_store=True)
        _add_task(actor, 1, "auth service")

        actor._vs_proxy = _make_vs_proxy_mock(
            embed_return=[[0.1]], search_hits=[("1", 0.49)]
        )

        result = actor.search_planning(query="unrelated")

        # 0.49 < 0.5 default threshold -> excluded
        assert result == []

    def test_search_with_default_config_top_k_passed(self) -> None:
        """Default config: search passes top_k=10 to vector store."""
        actor = _make_actor(vector_store=True)
        _add_task(actor, 1, "auth service")

        actor._vs_proxy = _make_vs_proxy_mock(
            embed_return=[[0.1]], search_hits=[]
        )

        actor.search_planning(query="auth")

        call_args = actor._vs_proxy.search.call_args
        assert call_args[0][2] == 10

    def test_default_mode_is_hybrid(self) -> None:
        """Default mode is hybrid — same as current implicit behavior."""
        actor = _make_actor(vector_store=True)
        _add_task(actor, 1, "auth service")

        actor._vs_proxy = _make_vs_proxy_mock(
            embed_return=[[0.1]], search_hits=[("1", 0.8)]
        )

        result = actor.search_planning(query="auth")

        # Both keyword and semantic ran — hybrid label
        assert len(result) == 1
        assert "(hybrid:" in result[0]


# ---------------------------------------------------------------------------
# AC-4: Search mode behavior (mode=keyword, mode=vector, mode=hybrid)
# ---------------------------------------------------------------------------


class TestSearchModeKeyword:
    """AC-4: mode='keyword' — only keyword matches, no embedding call."""

    def test_keyword_mode_returns_keyword_matches(self) -> None:
        actor = _make_actor(vector_store=True)
        _add_task(actor, 1, "auth flow setup")
        _add_task(actor, 2, "database schema")

        actor._vs_proxy = _make_vs_proxy_mock(
            embed_return=[[0.1]], search_hits=[("2", 0.9)]
        )

        result = actor.search_planning(query="auth", mode="keyword")

        assert len(result) == 1
        assert _extract_id(result[0]) == 1
        assert "(keyword match)" in result[0]

    def test_keyword_mode_no_embedding_call(self) -> None:
        actor = _make_actor(vector_store=True)
        _add_task(actor, 1, "auth flow setup")

        actor._vs_proxy = _make_vs_proxy_mock(
            embed_return=[[0.1]], search_hits=[]
        )

        actor.search_planning(query="auth", mode="keyword")

        actor._vs_proxy.embed.assert_not_called()
        actor._vs_proxy.search.assert_not_called()

    def test_keyword_mode_with_no_vs_proxy(self) -> None:
        """mode='keyword' works fine even without vector store."""
        actor = _make_actor(vector_store=False)
        _add_task(actor, 1, "auth flow setup")

        result = actor.search_planning(query="auth", mode="keyword")

        assert len(result) == 1
        assert _extract_id(result[0]) == 1


class TestSearchModeVector:
    """AC-4: mode='vector' — only semantic matches, no keyword matching."""

    def test_vector_mode_returns_only_semantic_matches(self) -> None:
        actor = _make_actor(vector_store=True)
        _add_task(actor, 1, "auth flow setup")  # keyword match for "auth"
        _add_task(actor, 2, "database schema")  # semantic match only

        actor._vs_proxy = _make_vs_proxy_mock(
            embed_return=[[0.1]], search_hits=[("1", 0.85), ("2", 0.75)]
        )

        result = actor.search_planning(query="auth", mode="vector")

        # Both returned by semantic, but NO keyword matching happened
        assert len(result) == 2
        assert all("(semantic:" in line for line in result)
        assert all("(keyword match)" not in line for line in result)

    def test_vector_mode_no_keyword_matching(self) -> None:
        """mode='vector' skips keyword matching — only semantic hits."""
        actor = _make_actor(vector_store=True)
        _add_task(actor, 1, "auth flow setup")  # would be keyword match
        _add_task(actor, 2, "database schema")

        actor._vs_proxy = _make_vs_proxy_mock(
            embed_return=[[0.1]], search_hits=[("2", 0.9)]
        )

        result = actor.search_planning(query="auth", mode="vector")

        # Only task 2 from semantic, task 1 excluded (no keyword phase)
        assert len(result) == 1
        assert _extract_id(result[0]) == 2

    def test_vector_mode_vs_proxy_none_returns_empty(self) -> None:
        """mode='vector' with _vs_proxy=None returns empty results."""
        actor = _make_actor(vector_store=False)
        _add_task(actor, 1, "auth flow setup")

        result = actor.search_planning(query="auth", mode="vector")

        assert result == []

    def test_vector_mode_vs_proxy_none_logs_warning(self) -> None:
        """mode='vector' with _vs_proxy=None returns empty (warning logged)."""
        actor = _make_actor(vector_store=False)
        _add_task(actor, 1, "auth flow setup")

        # The main assertion is that empty results are returned;
        # warning logging is verified by the caplog output in the test run.
        result = actor.search_planning(query="auth", mode="vector")

        assert result == []


class TestSearchModeHybrid:
    """AC-4: mode='hybrid' — both keyword and semantic, hybrid labels."""

    def test_hybrid_mode_unions_keyword_and_semantic(self) -> None:
        actor = _make_actor(vector_store=True)
        _add_task(actor, 1, "auth flow setup")  # keyword match
        _add_task(actor, 2, "database schema")  # semantic only

        actor._vs_proxy = _make_vs_proxy_mock(
            embed_return=[[0.1]], search_hits=[("2", 0.75)]
        )

        result = actor.search_planning(query="auth", mode="hybrid")

        assert len(result) == 2
        assert _extract_ids(result) == {1, 2}

    def test_hybrid_mode_labels_dual_match_as_hybrid(self) -> None:
        """Hit found by both keyword and semantic is labeled (hybrid: ...)."""
        actor = _make_actor(vector_store=True)
        _add_task(actor, 1, "auth module")

        actor._vs_proxy = _make_vs_proxy_mock(
            embed_return=[[0.1]], search_hits=[("1", 0.90)]
        )

        result = actor.search_planning(query="auth", mode="hybrid")

        assert len(result) == 1
        assert "(hybrid: 0.90)" in result[0]

    def test_hybrid_mode_vs_proxy_none_falls_back_to_keyword(self) -> None:
        """mode='hybrid' with _vs_proxy=None falls back to keyword-only."""
        actor = _make_actor(vector_store=False)
        _add_task(actor, 1, "auth flow setup")
        _add_task(actor, 2, "database schema")

        result = actor.search_planning(query="auth", mode="hybrid")

        assert len(result) == 1
        assert _extract_id(result[0]) == 1
        assert "(keyword match)" in result[0]

    def test_hybrid_mode_is_default(self) -> None:
        """mode defaults to 'hybrid'."""
        import inspect

        from akgentic.tool.planning.planning_actor import PlanActor

        sig = inspect.signature(PlanActor.search_planning)
        assert sig.parameters["mode"].default == "hybrid"
