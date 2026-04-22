"""Validate public API exports from akgentic.infra."""

from __future__ import annotations


def test_infra_exports() -> None:
    """All declared __all__ symbols are importable from akgentic.infra."""
    from akgentic import infra

    for name in infra.__all__:
        assert hasattr(infra, name), f"Missing export: {name}"


def test_protocols_exports() -> None:
    """All declared __all__ symbols are importable from akgentic.infra.protocols."""
    from akgentic.infra import protocols

    for name in protocols.__all__:
        assert hasattr(protocols, name), f"Missing export: {name}"


def test_adapters_exports() -> None:
    """All declared __all__ symbols are importable from akgentic.infra.adapters."""
    from akgentic.infra import adapters

    for name in adapters.__all__:
        assert hasattr(adapters, name), f"Missing export: {name}"


def test_infra_all_includes_protocols() -> None:
    """akgentic.infra.__all__ includes all protocol exports."""
    from akgentic import infra
    from akgentic.infra import protocols

    for name in protocols.__all__:
        assert name in infra.__all__, f"Protocol {name} not in infra.__all__"


def test_infra_all_includes_adapters() -> None:
    """akgentic.infra.__all__ includes all adapter exports."""
    from akgentic import infra
    from akgentic.infra import adapters

    for name in adapters.__all__:
        assert name in infra.__all__, f"Adapter {name} not in infra.__all__"


def test_infra_all_includes_server_models() -> None:
    """akgentic.infra.__all__ includes ServerSettings, CommunitySettings, TierServices, CommunityServices."""
    from akgentic import infra

    assert "ServerSettings" in infra.__all__
    assert "CommunitySettings" in infra.__all__
    assert "TierServices" in infra.__all__
    assert "CommunityServices" in infra.__all__


def test_infra_all_includes_wiring() -> None:
    """akgentic.infra.__all__ includes wire_community."""
    from akgentic import infra

    assert "wire_community" in infra.__all__


def test_infra_all_includes_server_app_and_models() -> None:
    """akgentic.infra.__all__ includes create_app, request/response models, and TeamService."""
    from akgentic import infra

    expected = (
        "create_app",
        "CreateTeamRequest",
        "TeamResponse",
        "TeamListResponse",
        "TeamService",
        "SendMessageRequest",
        "HumanInputRequest",
        "EventResponse",
        "EventListResponse",
    )
    for name in expected:
        assert name in infra.__all__, f"Missing export: {name}"


def test_server_module_exports() -> None:
    """akgentic.infra.server.__all__ includes all server-layer symbols."""
    from akgentic.infra import server

    for name in server.__all__:
        assert hasattr(server, name), f"Missing export: {name}"
