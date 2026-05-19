"""Story 15.6 integration — end-to-end typed-instance splice against real fixtures.

Epic 15 Story 15.6 made ``populate_refs`` return a typed Pydantic instance at
every ref-marker position (built from the referenced entry's declared
``model_type``) so polymorphic parent fields (``AgentConfig.tools: list[ToolCard]``,
``TeamCard.message_types: list[type]``, …) validate against concrete subclasses
without requiring authors to repeat the FQCN inside payloads via ``__model__``
markers.

This module exercises the change against the trimmed ``agent-team-v1`` bundle
under ``packages/akgentic-catalog/tests/fixtures/`` — real ``ToolCard`` /
``AgentCard`` / ``TeamCard`` shapes with real ref chains (tool-ref → agent-ref
→ team-ref, plus a shared model-ref). AC #6 (``resolve`` returns a typed
``TeamCard``), AC #8 (``_check_transient_validation`` mirrors the resolve path),
AC #9 (end-to-end ``validate_namespace`` reports ``ok=True``), and the
polymorphic-field invariants (AC #1) are covered here; unit-level splice
semantics remain in :mod:`.test_populate_refs`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

# These imports exercise real ToolCard / AgentCard / TeamCard classes so the
# allowlisted model_types in the fixture resolve to live code.
import akgentic.tool  # noqa: F401 — load real ToolCard subclasses (search, planning, workspace)
import pytest

from akgentic.catalog.catalog import Catalog
from akgentic.catalog.repositories.yaml import YamlEntryRepository
from akgentic.catalog.resolver import populate_refs, resolve

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures"
"""Directory holding the trimmed ``agent-team-v1`` bundle used by this module."""


@pytest.fixture
def agent_team_v1_repo(tmp_path: Path) -> YamlEntryRepository:
    """Copy the fixture bundle into a tmp path and return a ``YamlEntryRepository``.

    Copying into ``tmp_path`` isolates the test from any repository write that
    might happen inside the assertion (``put`` would otherwise mutate the
    checked-in fixture). The copy is per-test via pytest's function-scoped
    ``tmp_path``.
    """
    dest = tmp_path / "catalog"
    shutil.copytree(FIXTURE_ROOT, dest)
    return YamlEntryRepository(dest)


class TestResolveTeamFromFixture:
    """AC #6 — ``resolve()`` on the team entry returns a typed ``TeamCard``.

    Story 15.6 is orthogonal to ``akgentic-core`` Story 9.1 (``AgentCard.config``
    coerces to the declared ``ConfigType``). Until 9.1 lands, the ``AgentCard``
    produced by ``resolve()`` carries a bare ``BaseConfig`` — so we do not walk
    the member tree to assert on ``config.tools`` here. The splice-level
    invariant (tool refs become typed ``ToolCard`` instances) is asserted
    directly on the populated payload in ``TestPopulateRefsTypedOnFixture``.
    """

    def test_team_entry_resolves_to_typed_team_card(
        self, agent_team_v1_repo: YamlEntryRepository
    ) -> None:
        from akgentic.team.models import TeamCard

        team_entry = agent_team_v1_repo.get("agent-team-v1", "team")
        assert team_entry is not None

        result = resolve(team_entry, agent_team_v1_repo)

        # The return-type widening is safe: cls.model_validate accepts a tree
        # containing nested typed instances and still produces a TeamCard at
        # the top level (AC #6).
        assert isinstance(result, TeamCard)
        assert result.name == "Agent Team"


class TestValidateNamespaceFromFixture:
    """AC #9 — full-namespace validation reports ``ok=True`` on the fixture bundle."""

    def test_validate_namespace_ok(self, agent_team_v1_repo: YamlEntryRepository) -> None:
        catalog = Catalog(agent_team_v1_repo)
        report = catalog.validate_namespace("agent-team-v1")
        # A green report means every Epic 15 + Story 15.6 check (allowlist,
        # per-entry model validation, cycle, type mismatch, lineage, ownership,
        # transient validation via populate_refs + model_validate) passed with
        # zero entry issues and zero global errors.
        assert report.ok, f"Namespace report was not ok: {report}"
        assert report.global_errors == []
        assert report.entry_issues == []


class TestPopulateRefsTypedOnFixture:
    """AC #1 — tool refs resolve to typed ``SearchTool``/``PlanningTool``/``WorkspaceTool``.

    Asserts directly on the tree returned by ``populate_refs`` — before any
    downstream ``AgentCard.model_validate`` runs — so this coverage is
    independent of ``akgentic-core`` Story 9.1 (``config`` coerced to the
    agent-class's declared ``ConfigType``).
    """

    @pytest.mark.parametrize("agent_id", ["assistant", "expert", "manager"])
    def test_agent_tool_refs_resolve_to_typed_instances(
        self, agent_team_v1_repo: YamlEntryRepository, agent_id: str
    ) -> None:
        from akgentic.tool.core import ToolCard
        from akgentic.tool.planning import PlanningTool
        from akgentic.tool.search import SearchTool
        from akgentic.tool.workspace import WorkspaceTool

        agent_entry = agent_team_v1_repo.get("agent-team-v1", agent_id)
        assert agent_entry is not None

        populated = populate_refs(agent_entry.payload, agent_team_v1_repo, agent_entry.namespace)
        tool_instances = populated["config"]["tools"]
        # Every tool in the populated tree is a concrete ToolCard subclass,
        # not a bare dict — this is the failure mode Story 15.6 fixes.
        for tool in tool_instances:
            assert isinstance(tool, ToolCard)
        # Order matches the fixture: web_search, planning, workspace.
        assert isinstance(tool_instances[0], SearchTool)
        assert isinstance(tool_instances[1], PlanningTool)
        assert isinstance(tool_instances[2], WorkspaceTool)
