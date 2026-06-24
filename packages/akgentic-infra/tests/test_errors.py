"""Unit tests for the ServerError base and the placement error hierarchy.

Covers ADR-031 §Decision 1 (base shape) and §Decision 2 (placement types: MRO,
isinstance, status/code, Retry-After), plus the backward-compatibility contract
that placement errors are still caught by ``except RuntimeError``.
"""

from __future__ import annotations

import pytest
from akgentic.infra.errors import PlacementConsistencyError, ServerError
from akgentic.infra.protocols.placement import (
    NoCapacityError,
    NoSandboxCapacityError,
    PlacementError,
    WorkerRejectedError,
)

_PLACEMENT_TYPES = [
    PlacementError,
    NoCapacityError,
    NoSandboxCapacityError,
    WorkerRejectedError,
]


class TestServerErrorBase:
    """AC #1, #2: the generic base carries its HTTP mapping as data."""

    def test_default_attrs(self) -> None:
        """Bare ServerError defaults to 500, no code, no headers."""
        err = ServerError("boom")
        assert err.status_code == 500
        assert err.code is None
        assert err.detail == "boom"
        assert err.headers is None

    def test_per_instance_overrides(self) -> None:
        """Keyword overrides set status_code / headers / code on the instance."""
        err = ServerError("boom", status_code=418, headers={"X-A": "1"}, code="teapot")
        assert err.status_code == 418
        assert err.code == "teapot"
        assert err.headers == {"X-A": "1"}

    def test_str_is_detail(self) -> None:
        """str(ServerError) is the detail passed to Exception.__init__."""
        assert str(ServerError("boom")) == "boom"

    def test_is_exception(self) -> None:
        """ServerError is a plain Exception (no Pydantic, no framework)."""
        assert isinstance(ServerError("boom"), Exception)


class TestPlacementErrorStatusAndCode:
    """AC #5: each placement type pins its status_code / code."""

    def test_placement_error(self) -> None:
        err = PlacementError("x")
        assert err.status_code == 503
        assert err.code == "placement_failed"

    def test_no_capacity_error(self) -> None:
        err = NoCapacityError("full")
        assert err.status_code == 503
        assert err.code == "no_worker_capacity"

    def test_no_sandbox_capacity_error(self) -> None:
        err = NoSandboxCapacityError("no sandbox")
        assert err.status_code == 503
        assert err.code == "no_sandbox_capacity"

    def test_worker_rejected_error(self) -> None:
        err = WorkerRejectedError("rejected")
        assert err.status_code == 502
        assert err.code == "worker_rejected"


class TestNoCapacityRetryAfter:
    """AC #5: NoCapacity attaches a default Retry-After; subclass inherits it."""

    def test_default_retry_after_attached(self) -> None:
        assert "Retry-After" in (NoCapacityError("full").headers or {})

    def test_sandbox_inherits_retry_after(self) -> None:
        assert "Retry-After" in (NoSandboxCapacityError("x").headers or {})

    def test_caller_headers_preserved(self) -> None:
        """A caller-supplied headers dict is not clobbered by the default."""
        err = NoCapacityError("full", headers={"X-Custom": "1"})
        assert err.headers == {"X-Custom": "1"}


class TestPlacementErrorMRO:
    """AC #6: MRO is PlacementError -> ServerError -> RuntimeError -> Exception."""

    def test_placement_error_mro(self) -> None:
        assert PlacementError.__mro__[:4] == (
            PlacementError,
            ServerError,
            RuntimeError,
            Exception,
        )

    @pytest.mark.parametrize("typ", _PLACEMENT_TYPES)
    def test_is_runtime_error_and_server_error(self, typ: type[PlacementError]) -> None:
        inst = typ("x")
        assert isinstance(inst, RuntimeError)
        assert isinstance(inst, ServerError)

    def test_subclass_isinstance_chain(self) -> None:
        assert isinstance(NoSandboxCapacityError("x"), NoCapacityError)
        assert isinstance(NoCapacityError("x"), PlacementError)


class TestBackwardCompatibility:
    """AC #15: existing ``except RuntimeError`` still catches placement errors."""

    def test_except_runtime_error_catches(self) -> None:
        caught = False
        try:
            raise NoCapacityError("full")
        except RuntimeError:
            caught = True
        assert caught


class TestPlacementConsistencyError:
    """AC #11: the post-create consistency error is a ServerError, not a placement one."""

    def test_status_and_code(self) -> None:
        err = PlacementConsistencyError("missing")
        assert err.status_code == 502
        assert err.code == "placement_consistency"

    def test_is_server_error_not_placement_error(self) -> None:
        err = PlacementConsistencyError("missing")
        assert isinstance(err, ServerError)
        assert not isinstance(err, PlacementError)


class TestModuleHygiene:
    """AC #3, #4: errors.py is FastAPI-free and not a Pydantic model."""

    def test_errors_module_has_no_web_framework_import(self) -> None:
        """The module's import statements pull in no FastAPI/Starlette/Pydantic."""
        import ast

        import akgentic.infra.errors as errors_module

        source = errors_module.__file__
        assert source is not None
        with open(source, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.append(node.module)
        # The only import is ``from __future__ import annotations``.
        assert imported == ["__future__"]

    def test_errors_module_declares_no_pydantic_config(self) -> None:
        """No ``arbitrary_types_allowed`` (Golden Rule #1b) — no Pydantic fields here."""
        import akgentic.infra.errors as errors_module

        source = errors_module.__file__
        assert source is not None
        with open(source, encoding="utf-8") as handle:
            assert "arbitrary_types_allowed" not in handle.read()

    def test_re_exported_from_protocols(self) -> None:
        from akgentic.infra.protocols import (
            NoCapacityError as ProtoNoCapacity,
        )
        from akgentic.infra.protocols import (
            PlacementError as ProtoPlacementError,
        )

        assert ProtoPlacementError is PlacementError
        assert ProtoNoCapacity is NoCapacityError

    def test_re_exported_from_top_level(self) -> None:
        import akgentic.infra as infra

        assert infra.ServerError is ServerError
        assert infra.PlacementError is PlacementError
        assert infra.WorkerRejectedError is WorkerRejectedError
