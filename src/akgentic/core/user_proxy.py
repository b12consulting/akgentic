"""UserProxy agent for human-in-the-loop workflows.

Migrated from akgentic-framework/libs/akgentic/akgentic/core/user_proxy.py
with v2 additions: ResultMessage routing and receiveMsg_UserMessage handler.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from akgentic.core.agent import Akgent
from akgentic.core.agent_config import BaseConfig
from akgentic.core.agent_state import BaseState
from akgentic.core.messages.message import Message, ResultMessage, UserMessage

if TYPE_CHECKING:
    from akgentic.core.actor_address import ActorAddress

logger = logging.getLogger(__name__)


class UserProxy(Akgent[BaseConfig, BaseState]):
    """Agent that enables human-in-the-loop workflows.

    UserProxy acts as a bridge between human users and the agent system.
    It receives messages from agents requesting human input, presents them
    to humans, and routes human responses back to the requesting agents.

    Usage:
        >>> from akgentic.core import ActorSystem, UserProxy, BaseConfig
        >>> system = ActorSystem()
        >>> config = BaseConfig(name="human", role="UserProxy")
        >>> proxy_addr = system.createActor(UserProxy, config=config)

        # Agent requests human input
        >>> worker.send(proxy_addr, UserMessage(content="What should I do next?"))

        # Human provides input via process_human_input
        >>> proxy_ref.proxy().process_human_input("Continue with option A", user_msg).get()

    Phase 3 Extension:
        Subclass UserProxy to integrate with UI systems:
        >>> class WebSocketUserProxy(UserProxy):
        ...     def receiveMsg_UserMessage(self, message: UserMessage, sender: ActorAddress):
        ...         self.websocket.send(message.content, reply_to=message)
    """

    def process_human_input(self, content: str, message: Message) -> None:
        """Process human input and route it back to the requesting agent.

        Called externally (e.g., from a UI layer) to inject human responses
        into the agent workflow. The default implementation creates a
        ResultMessage with the human's response and sends it to the original
        sender.

        Override this method in subclasses to customize response handling,
        validation, or routing logic for specific workflow requirements.

        Args:
            content: The human's text response.
            message: The original message from the requesting agent.
                     message.sender is used as the reply-to address.

        Example:
            >>> user_proxy.process_human_input("Approve the plan", original_msg)
        """
        logger.info(f"Received human input: {content}, at destination of: {message.sender}")
        response = ResultMessage(content=content)
        self.send(message.sender, response)

    def receiveMsg_UserMessage(  # noqa: N802
        self, message: UserMessage, sender: ActorAddress
    ) -> None:
        """Handle UserMessage from agents requesting human input.

        Default implementation logs the message. Override in subclasses
        to integrate with specific UI systems (WebSocket, REST API, CLI, etc.).

        Args:
            message: The UserMessage requesting human input.
            sender: The ActorAddress of the requesting agent.

        Example override:
            >>> class CLIUserProxy(UserProxy):
            ...     def receiveMsg_UserMessage(self, message: UserMessage, sender: ActorAddress):
            ...         response = input(f"Agent asks: {message.content}\\n> ")
            ...         self.process_human_input(response, message)
        """
        logger.info(
            f"UserProxy received message from {sender}: "
            f"{message.content if hasattr(message, 'content') else message}"
        )
