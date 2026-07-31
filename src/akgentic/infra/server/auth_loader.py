"""Config-driven auth-strategy loader for community wiring (ADR-035 Decision 7).

Community wiring selects a complete ``AuthStrategy`` by config string, discovered
via ``importlib.metadata`` entry points under the ``akgentic.infra.auth.strategies``
group. The default ``"noauth"`` short-circuits to infra's own ``NoAuth`` — no
entry-point lookup, no auth-library import — so community's dependency closure
stays ``authlib``/redis/dapr-free and byte-identical to today.

An unknown / non-``"noauth"`` name fails loud at wire time (never a silent
anonymous fallback), and the entry-point-produced strategy is validated against
the ``@runtime_checkable`` ``AuthStrategy`` Protocol before use. The resolved
entry-point callable is invoked with **no** arguments — infra passes it no
``akgentic-infra-auth``-typed object — keeping the loader agnostic to any
plugin's settings type (the paid tiers wire their factory directly and never use
this discovery path).
"""

from __future__ import annotations

import importlib.metadata

from akgentic.infra.adapters.community.no_auth import NoAuth
from akgentic.infra.protocols.auth import AuthStrategy

#: Entry-point group under which complete auth strategies register themselves.
AUTH_STRATEGY_GROUP = "akgentic.infra.auth.strategies"

#: The default community selector — the anonymous strategy, resolved without any
#: entry-point lookup or auth-library import.
NOAUTH = "noauth"


class UnknownAuthStrategyError(RuntimeError):
    """A non-``"noauth"`` strategy name could not be resolved to a valid strategy.

    Raised either when no entry point under :data:`AUTH_STRATEGY_GROUP` matches the
    requested name, or when the resolved entry point produced an object that does
    not satisfy the :class:`AuthStrategy` Protocol. In both cases the loader fails
    loud rather than returning an anonymous fallback.
    """


def load_auth_strategy(name: str) -> AuthStrategy:
    """Resolve a community auth-strategy *name* to an ``AuthStrategy``.

    The default ``"noauth"`` returns infra's own ``NoAuth`` directly — no
    entry-point lookup, no auth-library import — keeping community byte-identical
    and its dependency closure clean. Any other name is discovered via the
    :data:`AUTH_STRATEGY_GROUP` entry-point group, loaded to a zero-argument
    factory, invoked, and Protocol-checked before use.

    Args:
        name: Strategy selector from community config (default ``"noauth"``).

    Returns:
        An object satisfying the runtime-checkable ``AuthStrategy`` Protocol.

    Raises:
        UnknownAuthStrategyError: No entry point matches *name*, or the resolved
            entry point produced an object that is not an ``AuthStrategy``.
    """
    if name == NOAUTH:
        # Byte-identical default: no entry-point lookup, no auth-library import.
        return NoAuth()

    matches = importlib.metadata.entry_points(group=AUTH_STRATEGY_GROUP).select(name=name)
    if not matches:
        discoverable = sorted(
            ep.name for ep in importlib.metadata.entry_points(group=AUTH_STRATEGY_GROUP)
        )
        raise UnknownAuthStrategyError(
            f"No auth strategy named {name!r} under entry-point group "
            f"{AUTH_STRATEGY_GROUP!r}. Discoverable: {discoverable}."
        )

    factory = next(iter(matches)).load()
    strategy = factory()  # zero-arg registration convention; infra passes no settings type
    if not isinstance(strategy, AuthStrategy):
        raise UnknownAuthStrategyError(
            f"Entry point {name!r} under group {AUTH_STRATEGY_GROUP!r} produced "
            f"{type(strategy)!r}, which does not satisfy the AuthStrategy protocol."
        )
    return strategy
