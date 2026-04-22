"""CLI profile configuration package.

Public API for loading and resolving CLI profiles from ``~/.akgentic/config.yaml``.
Implements ADR-021 §Decision 1 (profile-declared auth; absence of ``auth`` means
"no auth") and §Decision 2 (config-only scope: no network/token code here).
"""

from akgentic.infra.cli.config.profile import (
    AmbiguousProfileError,
    AuthConfig,
    CliConfig,
    ConfigFileNotFoundError,
    MalformedConfigError,
    ProfileConfig,
    ProfileConfigError,
    UnknownProfileError,
    UnsupportedAuthTypeError,
    load_config,
    resolve_profile,
)

__all__ = [
    "AmbiguousProfileError",
    "AuthConfig",
    "CliConfig",
    "ConfigFileNotFoundError",
    "MalformedConfigError",
    "ProfileConfig",
    "ProfileConfigError",
    "UnknownProfileError",
    "UnsupportedAuthTypeError",
    "load_config",
    "resolve_profile",
]
