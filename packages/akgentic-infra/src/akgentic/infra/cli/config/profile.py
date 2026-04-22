"""Profile configuration models, loader, and active-profile resolver.

Implements ADR-021 §Decision 1 — profile-declared auth as the single source of
truth. The **absence** of the ``auth`` block is the runtime signal for "no
auth"; its **presence** is the signal to run OIDC. This module is the boundary
where that distinction becomes typed: downstream callers branch on
``profile.auth is None`` only.

Scope is intentionally narrow: pure configuration modeling + YAML loading +
profile selection. No HTTP client wiring, no token cache, no OIDC code.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, HttpUrl, ValidationError

# ---------------------------------------------------------------------------
# Typed error hierarchy
# ---------------------------------------------------------------------------


class ProfileConfigError(Exception):
    """Base class for all profile-config errors."""


class ConfigFileNotFoundError(ProfileConfigError):
    """Raised when the config file does not exist on disk."""


class MalformedConfigError(ProfileConfigError):
    """Raised when the config file cannot be parsed or fails top-level validation."""


class UnknownProfileError(ProfileConfigError):
    """Raised when a requested profile name is not in ``config.profiles``."""


class AmbiguousProfileError(ProfileConfigError):
    """Raised when no profile is selected and more than one is available."""


class UnsupportedAuthTypeError(ProfileConfigError):
    """Raised when ``auth.type`` is present but not in the supported set."""


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class AuthConfig(BaseModel):
    """Authentication config block for a profile.

    Only ``oidc`` is supported today; Pydantic's ``Literal`` rejects other
    values at validation time, which the loader translates to a clearer
    ``UnsupportedAuthTypeError``.
    """

    type: Literal["oidc"]
    issuer: HttpUrl
    client_id: str
    tenant: str | None = None


class ProfileConfig(BaseModel):
    """A single CLI profile.

    ``auth=None`` (the field's absence in YAML) is the signal that the CLI
    MUST NOT attach an ``Authorization`` header for this profile (ADR-021).
    """

    endpoint: HttpUrl
    auth: AuthConfig | None = None


class CliConfig(BaseModel):
    """Top-level config read from ``~/.akgentic/config.yaml``.

    ``profiles`` is the one allowed mapping: keys are profile names (strings),
    and values are typed ``ProfileConfig`` models — never ``Any``.
    """

    profiles: dict[str, ProfileConfig]
    default_profile: str | None = None


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


_DEFAULT_CONFIG_PATH = Path.home() / ".akgentic" / "config.yaml"


def _is_unsupported_auth_type_error(err: ValidationError) -> bool:
    """Return True if a ValidationError is caused by an invalid ``auth.type``.

    Pydantic raises ``literal_error`` when a ``Literal[...]`` field receives a
    value outside the allowed set. We narrow to errors whose location ends at
    ``('auth', 'type')`` for any profile.
    """
    for err_entry in err.errors():
        loc = err_entry.get("loc", ())
        err_type = err_entry.get("type", "")
        if err_type == "literal_error" and len(loc) >= 2 and loc[-2:] == ("auth", "type"):
            return True
    return False


def load_config(path: Path | None = None) -> CliConfig:
    """Load and validate the CLI config file.

    Args:
        path: Optional override path. Defaults to ``~/.akgentic/config.yaml``.

    Returns:
        A validated :class:`CliConfig`.

    Raises:
        ConfigFileNotFoundError: If the file does not exist.
        MalformedConfigError: If the YAML is invalid or fails validation.
        UnsupportedAuthTypeError: If ``auth.type`` is present but not ``"oidc"``.
    """
    resolved_path = path if path is not None else _DEFAULT_CONFIG_PATH

    if not resolved_path.exists():
        raise ConfigFileNotFoundError(f"Config file not found: {resolved_path}")

    try:
        raw_text = resolved_path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - defensive
        raise MalformedConfigError(f"Cannot read config file {resolved_path}: {exc}") from exc

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise MalformedConfigError(f"Invalid YAML in {resolved_path}: {exc}") from exc

    if data is None:
        raise MalformedConfigError(f"Config file is empty: {resolved_path}")

    if not isinstance(data, dict):
        raise MalformedConfigError(
            f"Config file top level must be a mapping, got {type(data).__name__}: {resolved_path}"
        )

    try:
        return CliConfig.model_validate(data)
    except ValidationError as exc:
        if _is_unsupported_auth_type_error(exc):
            raise UnsupportedAuthTypeError(
                f"Unsupported auth.type in {resolved_path}: only 'oidc' is supported"
            ) from exc
        raise MalformedConfigError(f"Invalid config in {resolved_path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


_ENV_PROFILE_VAR = "AKGENTIC_PROFILE"


def _lookup_profile(config: CliConfig, name: str) -> ProfileConfig:
    """Return the named profile or raise UnknownProfileError with available names."""
    if name not in config.profiles:
        available = sorted(config.profiles.keys())
        raise UnknownProfileError(f"Unknown profile: {name!r}. Available profiles: {available}")
    return config.profiles[name]


def resolve_profile(
    config: CliConfig,
    *,
    cli_profile: str | None,
    env: Mapping[str, str],
) -> tuple[str, ProfileConfig]:
    """Select the active profile from ``config`` using the documented precedence.

    Precedence (highest to lowest):

    1. ``cli_profile`` (typically from a ``--profile`` CLI flag)
    2. ``AKGENTIC_PROFILE`` in the provided ``env`` mapping
    3. ``config.default_profile``
    4. If exactly one profile exists, use it
    5. Otherwise raise :class:`AmbiguousProfileError`

    Args:
        config: Loaded :class:`CliConfig`.
        cli_profile: Profile name from the CLI flag, or ``None``.
        env: Environment mapping (pass a dict in tests; pass ``os.environ`` in
            production). Explicit parameter keeps this function pure.

    Returns:
        A ``(name, ProfileConfig)`` tuple.

    Raises:
        UnknownProfileError: When the selected name is not in ``config.profiles``.
        AmbiguousProfileError: When no selection criterion matches and more than
            one profile exists.
    """
    if cli_profile is not None:
        return cli_profile, _lookup_profile(config, cli_profile)

    env_name = env.get(_ENV_PROFILE_VAR)
    if env_name:
        return env_name, _lookup_profile(config, env_name)

    if config.default_profile is not None:
        return config.default_profile, _lookup_profile(config, config.default_profile)

    if len(config.profiles) == 1:
        name, profile = next(iter(config.profiles.items()))
        return name, profile

    available = sorted(config.profiles.keys())
    raise AmbiguousProfileError(
        "No profile selected (no --profile, no AKGENTIC_PROFILE, no default_profile) "
        f"and more than one profile is defined. Available profiles: {available}"
    )
