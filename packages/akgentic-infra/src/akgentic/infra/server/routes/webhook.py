"""Webhook route — inbound message ingestion from external interaction channels."""

from __future__ import annotations

import logging
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Request

from akgentic.infra.adapters.shared.channel_parser_registry import ChannelParserRegistry
from akgentic.infra.protocols.channels import (
    ChannelRegistry,
    InteractionChannelIngestion,
    JsonValue,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhook"])


def get_channel_parser_registry(request: Request) -> ChannelParserRegistry:
    """FastAPI dependency: extract ChannelParserRegistry from app.state."""
    return cast(ChannelParserRegistry, request.app.state.channel_parser_registry)


def get_channel_registry(request: Request) -> ChannelRegistry:
    """FastAPI dependency: extract ChannelRegistry from app.state."""
    return cast(ChannelRegistry, request.app.state.channel_registry)


def get_ingestion(request: Request) -> InteractionChannelIngestion:
    """FastAPI dependency: extract InteractionChannelIngestion from app.state."""
    return cast(InteractionChannelIngestion, request.app.state.ingestion)


@router.post("/{channel}", status_code=204)
async def webhook(
    channel: str,
    request: Request,
    parser_registry: ChannelParserRegistry = Depends(get_channel_parser_registry),
    channel_registry: ChannelRegistry = Depends(get_channel_registry),
    ingestion: InteractionChannelIngestion = Depends(get_ingestion),
) -> None:
    """Process an inbound webhook from an external interaction channel.

    Three routing flows based on parsed ChannelMessage:
    1. Reply: team_id is set → route_reply
    2. Continuation: no team_id but existing team found → route_reply
    3. Initiation: no existing team → initiate_team + register
    """
    content_type = request.headers.get("content-type", "")
    logger.info("POST /webhook/%s — content_type=%s", channel, content_type)

    parser = parser_registry.get_parser(channel)
    if parser is None:
        logger.warning("Unknown channel: %s", channel)
        raise HTTPException(status_code=404, detail=f"Unknown channel: {channel}")

    if "application/json" in content_type:
        payload: dict[str, JsonValue] = await request.json()
    elif "application/x-www-form-urlencoded" in content_type:
        form_data = await request.form()
        payload = {k: str(v) for k, v in form_data.items()}
    else:
        logger.warning("Unsupported content type: %s", content_type)
        raise HTTPException(status_code=415, detail="Unsupported content type")
    message = await parser.parse(payload)

    if message.team_id is not None:
        # Reply flow
        logger.debug("Webhook reply: channel=%s, team_id=%s", channel, message.team_id)
        await ingestion.route_reply(message.team_id, message.content, message.message_id)
    else:
        existing_team = await channel_registry.find_team(channel, message.channel_user_id)
        if existing_team is not None:
            # Continuation flow
            logger.debug(
                "Webhook continuation: channel=%s, user=%s, team_id=%s",
                channel,
                message.channel_user_id,
                existing_team,
            )
            await ingestion.route_reply(existing_team, message.content)
        else:
            # Initiation flow
            new_team_id = await ingestion.initiate_team(
                message.content,
                message.channel_user_id,
                parser.default_catalog_entry,
            )
            logger.debug(
                "Webhook initiation: channel=%s, user=%s, new_team=%s",
                channel,
                message.channel_user_id,
                new_team_id,
            )
            await channel_registry.register(channel, message.channel_user_id, new_team_id)
