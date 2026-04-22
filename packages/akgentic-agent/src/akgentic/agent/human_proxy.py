"""HumanProxy agent with continuation-aware message handling.

Extends akgentic-core's UserProxy to provide team-aware human interaction with
automatic answer routing through continuation chains. Enables human-in-the-loop
workflows where agents can request human input and receive answers that properly
route back through multi-agent chains.

Migrated from V1: akgentic-framework/libs/akgentic-agent/akgentic_team/core/human_proxy.py
"""

from __future__ import annotations

import logging

from akgentic.agent.messages import AgentMessage
from akgentic.core import ActorAddress, UserProxy
from akgentic.core.messages.message import Message

logger = logging.getLogger(__name__)


class HumanProxy(UserProxy):
    """UserProxy with team message handling and continuation support.

    HumanProxy extends UserProxy with:
    - receiveMsg_HelpRequestMessage: Receives help requests from team agents
    - receiveMsg_ResultMessage: Receives final results to display to human
    - process_human_input: Routes human responses back through continuation chains

    The continuation system enables multi-hop request/answer routing:
    Manager → Dev → Tester → Dev → Manager → Human → Manager → ...

    Usage:
        >>> from akgentic.core import ActorSystem
        >>> from akgentic.core.agent_config import BaseConfig
        >>> from akgentic.agent import HumanProxy
        >>>
        >>> system = ActorSystem()
        >>> config = BaseConfig(name="human", role="HumanProxy")
        >>> proxy_addr = system.createActor(HumanProxy, config=config)
        >>>
        >>> # Agent sends help request
        >>> agent.send(proxy_addr, HelpRequestMessage(...))
        >>>
        >>> # Human responds
        >>> proxy_ref.proxy().process_human_input("My answer", help_req).get()

    Attributes:
        Inherits config and state from UserProxy (BaseConfig, BaseState).
    """

    def receiveMsg_AgentMessage(  # noqa: N802
        self, message: AgentMessage, sender: ActorAddress
    ) -> None:
        """Handle AgentMessage from agents - store for human processing.

        When an agent needs human input, it sends a AgentMessage. This
        method receives and stores the request for human processing. The human's
        response will be sent back via process_human_input().

        Args:
            message: The AgentMessage from an agent.
            sender: The ActorAddress of the requesting agent.
        """
        logger.info(f"[Message from {sender}] {message.content}")
        # In a real implementation, this would:
        # - Queue the request for human review
        # - Send to UI for human response
        # - Store request context for continuation tracking
        # For now, just log it - human responds via process_human_input()

    def process_human_input(self, content: str, message: Message) -> None:
        """Process human input and route back through continuation chain.

        Takes human's response to an AgentMessage and creates a new AgentMessage
        that routes back to the requesting agent via the continuation chain. The
        recipient (destinataire) is derived from ``message.sender`` -- i.e. the
        agent that originally sent the incoming message is the one that receives
        the human's answer. Supports multi-hop routing where answers automatically
        flow back through multiple agents.

        This method simulates the ``on_receive()`` lifecycle by setting
        ``_current_message`` before calling ``send()`` (so that ``parent_id``
        tracking works correctly) and clearing it afterwards.

        Args:
            content: The human's response text.
            message: The original AgentMessage being answered. Its ``sender``
                field determines the recipient of the outgoing response.

        Example:
            >>> # Agent A asks human for input
            >>> help_req = AgentMessage(content="Should we proceed?", ...)
            >>> # Human responds
            >>> human_proxy.process_human_input("Yes, proceed", help_req)
            >>> # AgentMessage routes back to Agent A (message.sender)
        """

        # Simulate the on_receive() lifecycle so that send() can read
        # _current_message for parent_id tracking.
        self._current_message = message
        try:
            # The sender of the AgentMessage is who we route the answer back to
            # This is the agent who directly asked for human input
            destinataire = message.sender
            assert destinataire is not None, "AgentMessage must have a sender"

            # Send the answer back to the requesting agent
            # The continuation chain ensures it routes correctly even in multi-hop scenarios
            self.send(
                destinataire,
                AgentMessage(
                    content=content,
                    type="response",
                    recipient=destinataire,
                ),
            )
        finally:
            self._current_message = None
