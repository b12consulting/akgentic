"""Channel parser registry — resolves and holds channel parsers/adapters from config."""

from __future__ import annotations

import importlib
import logging

from pydantic import BaseModel, Field

from akgentic.infra.protocols.channels import (
    ChannelParser,
    InteractionChannelAdapter,
)

logger = logging.getLogger(__name__)


class ChannelConfig(BaseModel):
    """Configuration for a single interaction channel."""

    parser_fqcn: str = Field(description="Fully-qualified class name of the ChannelParser")
    adapter_fqcn: str = Field(
        description="Fully-qualified class name of the InteractionChannelAdapter"
    )
    config: dict[str, str] = Field(
        default_factory=dict,
        description="Extra kwargs passed to parser and adapter constructors",
    )


def import_class(fqcn: str) -> type:
    """Dynamically import a class by its fully-qualified dotted name.

    Args:
        fqcn: Fully-qualified class name, e.g. "acme.parsers.WhatsAppParser".

    Returns:
        The resolved class object.

    Raises:
        ImportError: If the module or class cannot be found.
    """
    module_path, _, class_name = fqcn.rpartition(".")
    if not module_path:
        msg = f"Invalid FQCN '{fqcn}': no module path"
        raise ImportError(msg)
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        msg = f"Module '{module_path}' not found for FQCN '{fqcn}'"
        raise ImportError(msg) from exc
    try:
        cls: type = getattr(module, class_name)
        return cls
    except AttributeError as exc:
        msg = f"Class '{class_name}' not found in module '{module_path}'"
        raise ImportError(msg) from exc


class ChannelParserRegistry:
    """Resolves FQCNs from channel configuration and holds parsers/adapters.

    Parsers are indexed by ``channel_name``; adapters are collected into a list
    for use by ``InteractionChannelDispatcher`` (story 4.2).
    """

    def __init__(self, channels_config: dict[str, ChannelConfig]) -> None:
        self._parsers: dict[str, ChannelParser] = {}
        self._adapters: list[InteractionChannelAdapter] = []
        self._load(channels_config)

    def _load(self, channels_config: dict[str, ChannelConfig]) -> None:
        """Resolve FQCNs and instantiate parsers and adapters."""
        for _channel_key, cfg in channels_config.items():
            logger.info(
                "Loading channel: %s (parser=%s, adapter=%s)",
                _channel_key,
                cfg.parser_fqcn,
                cfg.adapter_fqcn,
            )
            parser_cls = import_class(cfg.parser_fqcn)
            adapter_cls = import_class(cfg.adapter_fqcn)

            parser = parser_cls(**cfg.config)
            if not isinstance(parser, ChannelParser):
                msg = f"Class '{cfg.parser_fqcn}' does not satisfy ChannelParser protocol"
                raise TypeError(msg)

            adapter = adapter_cls(**cfg.config)
            if not isinstance(adapter, InteractionChannelAdapter):
                msg = (
                    f"Class '{cfg.adapter_fqcn}' does not satisfy "
                    f"InteractionChannelAdapter protocol"
                )
                raise TypeError(msg)

            self._parsers[parser.channel_name] = parser
            self._adapters.append(adapter)
        logger.debug("Channel parser registry loaded: %d channel(s)", len(self._parsers))

    def get_parser(self, channel_name: str) -> ChannelParser | None:
        """Return the parser for the given channel name, or None."""
        parser = self._parsers.get(channel_name)
        logger.debug("Parser lookup: channel=%s, found=%s", channel_name, parser is not None)
        return parser

    def get_adapters(self) -> list[InteractionChannelAdapter]:
        """Return all resolved adapters."""
        return list(self._adapters)

    def channel_names(self) -> list[str]:
        """Return all registered channel names."""
        return list(self._parsers.keys())
