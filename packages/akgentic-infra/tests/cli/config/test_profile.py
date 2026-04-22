"""Unit tests for ``akgentic.infra.cli.config.profile``.

Covers every behavior listed in Story 21.1 AC #8:
  - precedence rules of resolve_profile
  - loader error handling (missing file, malformed YAML, validation, auth type)
  - typed model parsing (auth absent vs present)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import HttpUrl

from akgentic.infra.cli.config import (
    AmbiguousProfileError,
    AuthConfig,
    CliConfig,
    ConfigFileNotFoundError,
    MalformedConfigError,
    ProfileConfig,
    UnknownProfileError,
    UnsupportedAuthTypeError,
    load_config,
    resolve_profile,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_profile(endpoint: str = "https://api.example.com") -> ProfileConfig:
    return ProfileConfig(endpoint=HttpUrl(endpoint))


def _make_profile_with_auth(
    endpoint: str = "https://api.example.com",
    issuer: str = "https://auth.example.com",
    client_id: str = "cli",
    tenant: str | None = None,
) -> ProfileConfig:
    return ProfileConfig(
        endpoint=HttpUrl(endpoint),
        auth=AuthConfig(
            type="oidc",
            issuer=HttpUrl(issuer),
            client_id=client_id,
            tenant=tenant,
        ),
    )


def _multi_profile_config(default_profile: str | None = None) -> CliConfig:
    return CliConfig(
        profiles={
            "alpha": _make_profile("https://alpha.example.com"),
            "beta": _make_profile("https://beta.example.com"),
        },
        default_profile=default_profile,
    )


def _write_yaml(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# resolve_profile — precedence (AC #5, #8)
# ---------------------------------------------------------------------------


def test_resolve_profile_cli_flag_wins_over_env_default_and_single() -> None:
    """--profile beats env, default_profile, and single-profile auto-select."""
    config = CliConfig(
        profiles={
            "alpha": _make_profile("https://alpha.example.com"),
            "beta": _make_profile("https://beta.example.com"),
        },
        default_profile="alpha",
    )
    env = {"AKGENTIC_PROFILE": "alpha"}

    name, profile = resolve_profile(config, cli_profile="beta", env=env)

    assert name == "beta"
    assert str(profile.endpoint) == "https://beta.example.com/"


def test_resolve_profile_env_wins_over_default_and_single() -> None:
    """AKGENTIC_PROFILE beats default_profile and single-profile auto-select."""
    config = _multi_profile_config(default_profile="alpha")
    env = {"AKGENTIC_PROFILE": "beta"}

    name, _profile = resolve_profile(config, cli_profile=None, env=env)

    assert name == "beta"


def test_resolve_profile_default_wins_over_single_auto_select() -> None:
    """default_profile beats single-profile auto-select when multiple profiles exist."""
    config = _multi_profile_config(default_profile="beta")

    name, _profile = resolve_profile(config, cli_profile=None, env={})

    assert name == "beta"


def test_resolve_profile_single_profile_auto_select() -> None:
    """Single-profile config auto-selects that profile when nothing else is provided."""
    config = CliConfig(profiles={"solo": _make_profile()})

    name, profile = resolve_profile(config, cli_profile=None, env={})

    assert name == "solo"
    assert str(profile.endpoint) == "https://api.example.com/"


def test_resolve_profile_ambiguous_raises() -> None:
    """Ambiguous selection (>=2 profiles, no flag/env/default) raises AmbiguousProfileError."""
    config = _multi_profile_config()

    with pytest.raises(AmbiguousProfileError) as err:
        resolve_profile(config, cli_profile=None, env={})

    # Error message must list available profiles (operator-actionable).
    assert "alpha" in str(err.value)
    assert "beta" in str(err.value)


def test_resolve_profile_unknown_cli_profile_raises() -> None:
    """Unknown name from --profile raises UnknownProfileError with available list."""
    config = _multi_profile_config()

    with pytest.raises(UnknownProfileError) as err:
        resolve_profile(config, cli_profile="missing", env={})

    assert "missing" in str(err.value)
    assert "alpha" in str(err.value)


def test_resolve_profile_unknown_env_profile_raises() -> None:
    """Unknown name from env var raises UnknownProfileError."""
    config = _multi_profile_config()

    with pytest.raises(UnknownProfileError):
        resolve_profile(config, cli_profile=None, env={"AKGENTIC_PROFILE": "missing"})


def test_resolve_profile_unknown_default_profile_raises() -> None:
    """Unknown name in default_profile raises UnknownProfileError."""
    # default_profile set to a name not present in profiles — validates via resolver
    # (not at model-construction time, since Pydantic doesn't cross-check).
    config = CliConfig(
        profiles={"alpha": _make_profile()},
        default_profile="ghost",
    )

    with pytest.raises(UnknownProfileError):
        resolve_profile(config, cli_profile=None, env={})


def test_resolve_profile_empty_env_string_ignored() -> None:
    """An empty AKGENTIC_PROFILE string is treated as absent, not as a profile name."""
    config = CliConfig(profiles={"solo": _make_profile()})

    name, _profile = resolve_profile(config, cli_profile=None, env={"AKGENTIC_PROFILE": ""})

    # Falls through to single-profile auto-select rather than raising UnknownProfile.
    assert name == "solo"


# ---------------------------------------------------------------------------
# load_config — file handling and parsing (AC #4, #6, #8)
# ---------------------------------------------------------------------------


def test_load_config_missing_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "does-not-exist.yaml"

    with pytest.raises(ConfigFileNotFoundError) as err:
        load_config(path)

    assert str(path) in str(err.value)


def test_load_config_malformed_yaml_raises(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "profiles:\n  alpha: {endpoint: [unclosed\n")

    with pytest.raises(MalformedConfigError):
        load_config(path)


def test_load_config_empty_file_raises(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "")

    with pytest.raises(MalformedConfigError):
        load_config(path)


def test_load_config_non_mapping_top_level_raises(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "- just\n- a\n- list\n")

    with pytest.raises(MalformedConfigError):
        load_config(path)


def test_load_config_validation_error_raises_malformed(tmp_path: Path) -> None:
    """A non-URL endpoint (not an auth-type problem) raises MalformedConfigError."""
    path = _write_yaml(
        tmp_path,
        "profiles:\n  alpha:\n    endpoint: not-a-url\n",
    )

    with pytest.raises(MalformedConfigError):
        load_config(path)


def test_load_config_no_auth_block_yields_none(tmp_path: Path) -> None:
    """Profile with no ``auth`` block parses cleanly and yields ``profile.auth is None``."""
    path = _write_yaml(
        tmp_path,
        "profiles:\n  alpha:\n    endpoint: https://api.example.com\n",
    )

    config = load_config(path)

    assert config.profiles["alpha"].auth is None


def test_load_config_with_auth_block_parses_typed(tmp_path: Path) -> None:
    """Profile ``with`` an auth block parses into a typed AuthConfig with validated URLs."""
    yaml_text = (
        "profiles:\n"
        "  alpha:\n"
        "    endpoint: https://api.example.com\n"
        "    auth:\n"
        "      type: oidc\n"
        "      issuer: https://auth.example.com\n"
        "      client_id: my-cli\n"
        "      tenant: acme\n"
    )
    path = _write_yaml(tmp_path, yaml_text)

    config = load_config(path)

    profile = config.profiles["alpha"]
    assert profile.auth is not None
    assert profile.auth.type == "oidc"
    assert str(profile.auth.issuer) == "https://auth.example.com/"
    assert profile.auth.client_id == "my-cli"
    assert profile.auth.tenant == "acme"


def test_load_config_auth_issuer_rejects_non_url(tmp_path: Path) -> None:
    """A non-URL issuer fails validation and surfaces as MalformedConfigError."""
    yaml_text = (
        "profiles:\n"
        "  alpha:\n"
        "    endpoint: https://api.example.com\n"
        "    auth:\n"
        "      type: oidc\n"
        "      issuer: not-a-url\n"
        "      client_id: my-cli\n"
    )
    path = _write_yaml(tmp_path, yaml_text)

    with pytest.raises(MalformedConfigError):
        load_config(path)


def test_load_config_unsupported_auth_type_raises(tmp_path: Path) -> None:
    """``auth.type`` outside the supported set raises UnsupportedAuthTypeError."""
    yaml_text = (
        "profiles:\n"
        "  alpha:\n"
        "    endpoint: https://api.example.com\n"
        "    auth:\n"
        "      type: saml\n"
        "      issuer: https://auth.example.com\n"
        "      client_id: my-cli\n"
    )
    path = _write_yaml(tmp_path, yaml_text)

    with pytest.raises(UnsupportedAuthTypeError):
        load_config(path)


def test_load_config_default_profile_roundtrip(tmp_path: Path) -> None:
    """``default_profile`` is parsed when present."""
    yaml_text = (
        "default_profile: alpha\nprofiles:\n  alpha:\n    endpoint: https://api.example.com\n"
    )
    path = _write_yaml(tmp_path, yaml_text)

    config = load_config(path)

    assert config.default_profile == "alpha"


def test_load_config_default_path_used_when_none_passed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """load_config() with no args reads the default ``~/.akgentic/config.yaml``."""
    fake_home = tmp_path / "home"
    (fake_home / ".akgentic").mkdir(parents=True)
    (fake_home / ".akgentic" / "config.yaml").write_text(
        "profiles:\n  alpha:\n    endpoint: https://api.example.com\n",
        encoding="utf-8",
    )

    # Patch the module-level constant so we don't depend on the real HOME.
    from akgentic.infra.cli.config import profile as profile_mod

    monkeypatch.setattr(
        profile_mod, "_DEFAULT_CONFIG_PATH", fake_home / ".akgentic" / "config.yaml"
    )

    config = load_config()
    assert "alpha" in config.profiles
