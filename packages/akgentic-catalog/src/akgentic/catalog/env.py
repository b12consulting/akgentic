"""Environment variable substitution utility.

Provides runtime resolution of ``${VAR}`` patterns in catalog values.
The catalog stores ``${VAR}`` as-is; this utility is called by runtime
consumers (e.g. create-team), not by catalog services.
"""

from __future__ import annotations

import logging
import os
import re

__all__ = [
    "resolve_env_vars",
]

logger = logging.getLogger(__name__)


def resolve_env_vars(value: str) -> str:
    """Replace ${VAR} patterns with environment variable values.

    This is a runtime utility -- the catalog stores ${VAR} as-is.
    Called by runtime consumers (e.g. create-team), not by catalog services.

    Args:
        value: String potentially containing ``${VAR}`` placeholders.

    Returns:
        The input string with all ``${VAR}`` patterns replaced by their
        corresponding environment variable values.

    Raises:
        OSError: If a referenced environment variable is not set.
    """

    def replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        result = os.environ.get(var_name)
        if result is None:
            raise OSError(f"Environment variable '{var_name}' not set")
        logger.debug("resolved env var %s", var_name)
        return result

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, value)
