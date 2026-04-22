"""Tests for the FastAPI lifespan handler (Story 14.3, AC #9)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from akgentic.infra.server.app import _lifespan


def _make_app_state(
    *,
    pre_drain_delay: int = 0,
    drain_timeout: int = 30,
) -> MagicMock:
    """Build a fake ``app`` with a ``state`` namespace mimicking _store_state()."""
    settings = SimpleNamespace(
        shutdown_pre_drain_delay=pre_drain_delay,
        shutdown_drain_timeout=drain_timeout,
    )
    connection_manager = AsyncMock()
    connection_manager.disconnect_all = AsyncMock()

    worker_handle = MagicMock()
    worker_handle.stop_all = MagicMock()
    services = SimpleNamespace(worker_handle=worker_handle)

    app = MagicMock()
    app.state = SimpleNamespace(
        settings=settings,
        connection_manager=connection_manager,
        services=services,
    )
    return app


class TestLifespanStartup:
    """Tests for lifespan startup phase (AC #9: draining=False on startup)."""

    @pytest.mark.asyncio
    async def test_startup_sets_draining_false(self) -> None:
        """AC #2: On startup, app.state.draining is set to False."""
        app = _make_app_state()
        ctx = _lifespan(app)
        await ctx.__aenter__()
        assert app.state.draining is False
        await ctx.__aexit__(None, None, None)


class TestLifespanShutdown:
    """Tests for lifespan shutdown phase (AC #9)."""

    @pytest.mark.asyncio
    async def test_shutdown_sets_draining_true(self) -> None:
        """AC #3: On shutdown, app.state.draining is set to True."""
        app = _make_app_state()
        ctx = _lifespan(app)
        await ctx.__aenter__()
        await ctx.__aexit__(None, None, None)
        assert app.state.draining is True

    @pytest.mark.asyncio
    async def test_shutdown_calls_disconnect_all_before_stop_all(self) -> None:
        """AC #3, #5: disconnect_all is called before stop_all in shutdown."""
        app = _make_app_state()
        call_order: list[str] = []

        async def mock_disconnect_all() -> None:
            call_order.append("disconnect_all")

        def mock_stop_all() -> None:
            call_order.append("stop_all")

        app.state.connection_manager.disconnect_all = mock_disconnect_all
        app.state.services.worker_handle.stop_all = mock_stop_all

        ctx = _lifespan(app)
        await ctx.__aenter__()
        await ctx.__aexit__(None, None, None)

        assert call_order == ["disconnect_all", "stop_all"]

    @pytest.mark.asyncio
    async def test_shutdown_sleeps_when_pre_drain_delay_positive(self) -> None:
        """AC #9: shutdown calls asyncio.sleep with shutdown_pre_drain_delay when > 0."""
        app = _make_app_state(pre_drain_delay=5)

        with patch("akgentic.infra.server.app.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            ctx = _lifespan(app)
            await ctx.__aenter__()
            await ctx.__aexit__(None, None, None)
            mock_sleep.assert_awaited_once_with(5)

    @pytest.mark.asyncio
    async def test_shutdown_skips_sleep_when_pre_drain_delay_zero(self) -> None:
        """AC #9: shutdown skips sleep when shutdown_pre_drain_delay == 0."""
        app = _make_app_state(pre_drain_delay=0)

        with patch("akgentic.infra.server.app.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            ctx = _lifespan(app)
            await ctx.__aenter__()
            await ctx.__aexit__(None, None, None)
            mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_shutdown_calls_shutdown_reader_pool(self) -> None:
        """Lifespan teardown releases the dedicated WS reader pool (issue #227)."""
        app = _make_app_state()

        with patch("akgentic.infra.server.app.shutdown_reader_pool") as mock_shutdown_pool:
            ctx = _lifespan(app)
            await ctx.__aenter__()
            await ctx.__aexit__(None, None, None)

        mock_shutdown_pool.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_shutdown_logs_timeout_warning(self) -> None:
        """AC #9: shutdown logs warning when stop_all exceeds drain timeout."""
        app = _make_app_state(drain_timeout=0)

        # Make stop_all block long enough to exceed a 0s timeout
        async def _slow_to_thread(fn: object) -> None:
            await asyncio.sleep(10)

        with (
            patch(
                "akgentic.infra.server.app.asyncio.to_thread",
                side_effect=_slow_to_thread,
            ),
            patch("akgentic.infra.server.app.logger") as mock_logger,
        ):
            ctx = _lifespan(app)
            await ctx.__aenter__()
            await ctx.__aexit__(None, None, None)
            # Verify warning was logged about timeout
            warning_calls = [
                c for c in mock_logger.warning.call_args_list if "exceeded" in str(c).lower()
            ]
            assert len(warning_calls) == 1
