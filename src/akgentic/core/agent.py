"""Agent base class with v1-compatible message dispatch pattern.

Provides the Akgent base class that agents extend to handle messages
using the receiveMsg_<Type> pattern from v1. Integrates with pykka actors
and provides state management, child actor creation, and orchestrator telemetry.

Source: Migrated from akgentic-framework/libs/akgentic/akgentic/core/akgent_impl.py
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import traceback as tb_module
import uuid
from types import TracebackType
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast, override

import pykka

from akgentic.core.actor_address import ActorAddress
from akgentic.core.actor_address_impl import ActorAddressImpl
from akgentic.core.agent_card import AgentCard
from akgentic.core.agent_config import BaseConfig
from akgentic.core.agent_state import BaseState
from akgentic.core.messages.message import Message, StopRecursively
from akgentic.core.messages.orchestrator import (
    ErrorMessage,
    EventMessage,
    ProcessedMessage,
    ReceivedMessage,
    SentMessage,
    StartMessage,
    StateChangedMessage,
    StopMessage,
)
from akgentic.core.utils.deserializer import DeserializeContext, deserialize_object

if TYPE_CHECKING:
    from akgentic.core.actor_address_impl import ActorAddressProxy, ActorAddressStopped
    from akgentic.core.orchestrator import Orchestrator


class WarningError(Exception):
    """Custom exception type for non-critical warnings that should not trigger error telemetry."""

    pass


# Type variables for generic Agent configuration and state
ConfigType = TypeVar("ConfigType", bound=BaseConfig)
StateType = TypeVar("StateType", bound="BaseState")
AkgentType = TypeVar("AkgentType", bound="Akgent[Any, Any]")

# Logger for agent operations
logger = logging.getLogger(__name__)


class AkgentDeserializeContext(DeserializeContext):
    """Deserialization context that resolves addresses via an Akgent's orchestrator.

    Used during state deserialization to reconstruct ActorAddress references
    by querying the orchestrator for live actor instances.
    """

    def __init__(self, akgent: Akgent[Any, Any]) -> None:
        """Initialize context with agent reference.

        Args:
            akgent: The Akgent instance providing orchestrator access.
        """
        self.akgent = akgent

    @override
    def resolve_address(
        self, address_dict: Any
    ) -> ActorAddress | ActorAddressProxy | ActorAddressStopped:
        """Resolve address dictionary to live ActorAddress or proxy.

        Args:
            address_dict: Serialized address with agent_id and metadata.

        Returns:
            Live ActorAddress if available, otherwise stopped/proxy address.
        """
        # Lazy import to avoid circular dependencies
        from akgentic.core.actor_address_impl import ActorAddressStopped

        address_stopped = ActorAddressStopped(address_dict)
        orch = self.akgent._orchestrator
        if orch is None:
            return address_stopped

        agent_id = str(address_dict["agent_id"])
        member = self.akgent.proxy_ask(orch, Orchestrator).get_team_member(agent_id)
        return member if member else address_stopped


class ProxyWrapper:
    """Wrapper around Pykka proxy supporting tell and ask patterns.

    Provides unified interface for fire-and-forget (tell) and blocking (ask)
    actor method invocations with automatic future resolution.
    """

    def __init__(
        self, actor: ActorAddress, ask_mode: bool = False, timeout: float | None = None
    ) -> None:
        """Initialize proxy wrapper.

        Args:
            actor: Target actor address to wrap.
            ask_mode: If True, use ask pattern; if False, use tell pattern.
            timeout: Optional timeout for ask operations in seconds.
        """
        self._pykka_proxy = cast(ActorAddressImpl, actor)._actor_ref.proxy()
        self._ask_mode = ask_mode
        self._timeout = timeout

    def __getattr__(self, name: str) -> Any:
        """Proxy attribute access with automatic future handling.

        Args:
            name: Attribute or method name to access on proxied actor.

        Returns:
            For methods: Wrapped callable with ask/tell behavior.
            For attributes: Resolved value (ask mode) or future (tell mode).
        """
        attr = getattr(self._pykka_proxy, name)

        # If it's a callable (method)
        if hasattr(attr, "__call__"):
            if self._ask_mode:
                # Ask mode: call the method and wait for result

                def ask_wrapper(*args: Any, **kwargs: Any) -> Any:
                    future = attr(*args, **kwargs)
                    return future.get(timeout=self._timeout)

                return ask_wrapper
            else:
                # Tell mode: call the method without waiting

                def tell_wrapper(*args: Any, **kwargs: Any) -> None:
                    attr(*args, **kwargs)
                    return None

                return tell_wrapper

        # For non-callable attributes
        if self._ask_mode and hasattr(attr, "get"):
            # Ask mode: resolve immediately
            return attr.get(timeout=self._timeout)

        return attr

    def __repr__(self) -> str:
        """String representation of proxy wrapper.

        Returns:
            String showing wrapped proxy.
        """
        return f"<ProxyWrapper for {self._pykka_proxy}>"


class Akgent(pykka.ThreadingActor, Generic[ConfigType, StateType]):  # noqa: UP046
    """Base agent class with explicit initialization parameters.

    Generic actor supporting typed configuration and state. Uses MRO-based
    message dispatch pattern where subclasses define receiveMsg_<Type> handlers.

    Agents are initialized with explicit keyword arguments instead of positional
    args for clarity and maintainability.

    Type Parameters:
        ConfigType: Agent configuration type (subclass of BaseConfig).
        StateType: Agent state type (subclass of BaseState).

    Attributes:
        agent_id: Unique identifier for this agent instance.
        config: Public agent configuration.
        state: Current agent state with observer pattern.
        llm_context: LLM conversation history (Phase 2+).

    Example:
        >>> # Create agent with explicit keyword parameters
        >>> ref = MyAgent.start(
        ...     agent_id=uuid.uuid4(),
        ...     config=BaseConfig(name="worker"),
        ...     user_id=user_id,
        ...     team_id=team_id,
        ...     orchestrator=orch_address,
        ... )
    """

    def __init__(
        self,
        agent_id: uuid.UUID | None = None,
        team_id: uuid.UUID | None = None,
        config: ConfigType | None = None,
        user_id: uuid.UUID | None = None,
        user_email: str | None = None,
        parent: ActorAddress | None = None,
        orchestrator: ActorAddress | None = None,
        restoring: bool = False,
        **kwargs: Any,
    ) -> None:
        """Initialize agent with explicit configuration parameters.

        Args:
            agent_id: Unique identifier for this agent (defaults to uuid4()).
            team_id: Optional team UUID. Auto-generated if not provided.
            config: Public agent configuration.
            user_id: User identifier for context propagation.
            user_email: User email for context propagation.
            parent: Parent agent address for hierarchy tracking.
            orchestrator: Orchestrator address for telemetry.
            restoring: Whether restoring from snapshot (default: False).
            **kwargs: Additional arguments passed to pykka.ThreadingActor.

        Calls init() hook after initialization for custom setup.
        """
        super().__init__(**kwargs)

        ## Event loop for async operations (needed for Pykka actor threads)
        # CRITICAL: Create event loop BEFORE initializing agent/model to ensure
        # all async primitives (locks, events) in HTTP clients are bound to the correct loop
        self._event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._event_loop)

        self._current_message: Message | None = None
        self._children: list[ActorAddress] = []

        ## Initialize from explicit parameters
        self.agent_id: uuid.UUID = agent_id or uuid.uuid4()
        self.team_id: uuid.UUID = team_id if team_id is not None else uuid.uuid4()
        self.config: ConfigType = config or BaseConfig()  # type: ignore
        self._user_id = user_id
        self._user_email = user_email
        self._parent = parent
        self._orchestrator = orchestrator
        self._restoring = restoring
        self.state: StateType = BaseState()  # type: ignore

        ## set default name and role if not provided
        self.config.name = self.config.name or str(self._actor_ref)
        self.config.role = self.config.role or self.actor_ref.actor_class.__name__
        if self._orchestrator is not None:
            from akgentic.core.orchestrator import Orchestrator

            self.orchestrator_proxy_ask = self.proxy_ask(self._orchestrator, Orchestrator)

        self._notify_orchestrator(
            StartMessage(
                config=self.config,
                parent=self._parent,
            )
        )

    def on_start(self) -> None:
        """Custom initialization hook, to be overridden by subclasses.

        Called by pykka in the actor's own thread once the actor is
        started.  Use for agent-specific setup that requires the actor
        system to be fully initialized (e.g. spawning children,
        subscribing to state changes).
        """
        pass

    @property
    def myAddress(self) -> ActorAddress:  # noqa: N802
        """Get this agent's actor address.

        Returns:
            ActorAddress wrapping this agent's pykka actor reference.
        """
        return ActorAddressImpl(self.actor_ref)

    @property
    def orchestrator(self) -> ActorAddress | None:
        """Get the orchestrator address.

        Returns:
            ActorAddress of the orchestrator if available, None otherwise.
        """
        return self._orchestrator

    def get_mailbox(self) -> list[Message]:
        """Get list of pending messages in this agent's mailbox.

        Returns:
            List of Message objects currently in the mailbox queue.
        """
        return [
            envelope.message
            for envelope in list(self._actor_ref.actor_inbox.queue)  # type: ignore
            if isinstance(envelope.message, Message)
        ]

    def createActor(  # noqa: N802
        self,
        actor_class: type[Akgent[Any, Any]],
        agent_id: uuid.UUID | None = None,
        config: BaseConfig | None = None,
    ) -> ActorAddress:
        """Create a child actor with context propagation.

        Inherits squad_id, user_id, user_email, team_id from parent.
        Tracks child in internal list for recursive cleanup.

        Args:
            actor_class: Class of the agent to instantiate.
            agent_id: Optional UUID for the new agent (defaults to uuid4()).
            config: Configuration for the new agent.

        Returns:
            ActorAddress of the newly created child agent.

        Example:
            >>> child = self.createActor(
            ...     WorkerAgent,
            ...     agent_id=uuid.uuid4(),
            ...     config=WorkerConfig(name="worker-1")
            ... )
        """
        if config is None:
            config = BaseConfig()

        ## Priority on the new configuration for the squad_id over the parent one
        config.squad_id = config.squad_id or self.config.squad_id

        actor = actor_class.start(
            agent_id=agent_id,
            config=config,
            user_id=self._user_id,
            user_email=self._user_email,
            team_id=self.team_id,
            parent=self.myAddress,
            orchestrator=self._orchestrator,
        )
        actor_address = ActorAddressImpl(actor)
        self._children.append(actor_address)

        return actor_address

    ##
    ## Message routing
    ##
    def _notify_orchestrator(self, message: Message) -> None:
        """Send telemetry message to orchestrator if available.

        Args:
            message: Orchestrator telemetry message (Start/Received/Processed/etc).
        """
        orchestrator = self._orchestrator
        if orchestrator is not None and orchestrator.is_alive():
            message.init(
                self.myAddress,
                self.team_id,
                self._current_message,
            )
            cast(ActorAddressImpl, orchestrator)._actor_ref.tell(message)

    def send(self, recipient: ActorAddress | None, message: Any) -> Any:
        """Send a message to another actor with telemetry.

        Args:
            recipient: Target actor address (must not be None).
            message: Message to send (any type).

        Returns:
            The sent message.

        Raises:
            AssertionError: If recipient is None.
        """
        assert recipient is not None, "Recipient cannot be None"
        if isinstance(message, Message):
            message.init(
                self.myAddress,
                self.team_id,
                self._current_message,
            )
            self._notify_orchestrator(SentMessage(message=message, recipient=recipient))

        cast(ActorAddressImpl, recipient)._actor_ref.tell(message)
        return message

    @override
    def _handle_failure(
        self,
        exception_type: type[BaseException] | None,
        exception_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Handle actor failure by logging and notifying the orchestrator.

        Called by Pykka when an exception occurs during message processing
        and no reply_to is set. Logs the error, marks the current message
        as processed, and sends an ErrorMessage to the orchestrator.
        WarningErrors are silently acknowledged without escalation.

        Args:
            exception_type: The type of the raised exception (None if unavailable).
            exception_value: The exception instance (None if unavailable).
            traceback: The traceback object (None if unavailable).
        """
        if exception_type is not None and exception_value is not None:
            logger.error(
                f"[{self.config.name}] ERROR processing message: {exception_value!s}",
                exc_info=(exception_type, exception_value, traceback),
            )
        else:
            logger.error(f"[{self.config.name}] ERROR processing message: {exception_value!s}")

        # Capture current_message before clearing for use in ErrorMessage
        failed_message = self._current_message

        if self._current_message is not None:
            self._notify_orchestrator(ProcessedMessage(message_id=self._current_message.id))
            self._current_message = None

        if isinstance(exception_value, WarningError):
            return

        self._notify_orchestrator(
            ErrorMessage(
                exception_type=exception_type.__name__ if exception_type else "Unknown",
                exception_value=str(exception_value) if exception_value else "",
                current_message=failed_message,
                traceback="".join(tb_module.format_tb(traceback)) if traceback else None,
            )
        )

    @override
    def on_receive(self, message: Any) -> Any:
        """Telemetry sandwich for message processing.

        Logs message receipt, notifies orchestrator (Received/Processed),
        and dispatches to message handler.

        Args:
            message: Incoming message to process.

        Returns:
            Result from message handler.
        """
        logger.info(
            f"[{self.config.name}-{self.team_id}] receiveMessage: {message.__class__.__name__}"
        )
        if isinstance(message, Message):
            self._current_message = message
            self._notify_orchestrator(ReceivedMessage(message_id=message.id))
            result = self._receiveMessage(message, message.sender)
            self._notify_orchestrator(ProcessedMessage(message_id=message.id))
            self._current_message = None
            return result

        return self._receiveMessage(message, None)

    # _receiveMessage() walks two hierarchies (the message's MRO and the actor's MRO).
    # If your receiveMsg_<Type> decides not to handle a message, the only way to tell
    # the dispatcher to keep searching up the chain is to return self.SUPER.
    # Any other return value (including None) is treated as "handled," and the search stops
    SUPER = hash("SUPER")

    def _receiveMessage(self, message: Any, sender: Any) -> Any:  # noqa: N802
        """Dispatch message using MRO-based handler lookup.

        Walks message class MRO looking for receiveMsg_<Type> handlers.
        For each found handler, walks actor class MRO to find implementation.
        Stops when handler returns non-SUPER value.

        Args:
            message: Message to dispatch.
            sender: Sender address (may be None for non-Message types).

        Returns:
            Result from message handler, or None if no handler found.
        """
        for each in inspect.getmro(message.__class__):
            methodName = "receiveMsg_" + each.__name__  # noqa: N806
            if hasattr(self, methodName):
                for klass in inspect.getmro(self.__class__):
                    if hasattr(klass, methodName):
                        method = getattr(klass, methodName)
                        if "sender" in inspect.signature(method).parameters:
                            r = method(self, message, sender)
                        else:
                            r = method(self, message)
                        if r != self.SUPER:
                            return r

        ## Message type not handled
        logger.warning(
            f"[{self.config.name}] Unknown message {message.__class__.__name__} from {sender}"
        )

    ##
    ## Stopping handling
    ##
    def receiveMsg_StopRecursively(self, msg: StopRecursively) -> None:
        """Handle recursive stop message.

        Args:
            msg: StopRecursively message.
        """
        self.stop()

    def stop(self) -> None:
        """Stop this agent and all children recursively.

        Iterates children list and stops each child before stopping self.
        """
        logger.info(f"### [{self.config.name}] Stopping recursively ...")
        for child in self._children.copy():
            self._stop_child(child)
        super().stop()

    def _stop_child(self, child: ActorAddress) -> None:
        """Stop a single child actor.

        Args:
            child: Child actor address to stop.
        """
        if child.is_alive():
            self.proxy_ask(child, Akgent).stop()

        try:
            self._children.remove(child)
        except Exception:
            logger.error(f"ERROR: stop_child: Actor with reference {child} doesn't exist")

    def on_stop(self) -> None:
        """Cleanup hook called when actor stops.

        Notifies orchestrator of stop event.
        """
        if self._event_loop.is_running():
            self._event_loop.stop()
        self._event_loop.close()

        self._notify_orchestrator(StopMessage())
        logger.info(f"[{self.config.name}] Stopped.")

    ##
    ## Event
    ##
    def notify_event(self, event: object) -> None:
        self._notify_orchestrator(EventMessage(event=event))

    ##
    ## State
    ##
    def notify_state_change(self, state: BaseState) -> None:
        """Notify orchestrator of state change.

        Implements AkgentStateObserver protocol - called by BaseState when
        fields change, and by init_state() during state initialization.

        Args:
            state: New state (BaseState instance).
        """
        serializable_state = state.serializable_copy()
        self._notify_orchestrator(
            StateChangedMessage(state=serializable_state),
        )

    def update_state(self, updates: dict[str, Any]) -> None:
        """Update the state of the agent and notify the orchestrator.

        Merges updates into current state, deserializes, and reinitializes.

        Args:
            updates: Dictionary of state field updates.
        """
        try:
            state_data = {**self.state.model_dump(), **updates}
            state = deserialize_object(state_data, AkgentDeserializeContext(self))
            self.init_state(cast(StateType, state))
        except Exception as e:
            logger.error(f"Failed to update state: {e}")
            self._notify_orchestrator(
                ErrorMessage(
                    exception_type=type(e).__name__,
                    exception_value=str(e),
                    current_message=self._current_message,
                )
            )
            return

    def init_state(self, state: StateType) -> None:
        """Initialize the state of the agent and notify the orchestrator.

        Preserves observer reference across state replacement.

        Note: Used externally by state restoration logic (e.g., restart_team.py).

        Args:
            state: New state to initialize.
        """
        state._observer = self.state._observer
        self.state = state
        self.notify_state_change(self.state)

    def init_llm_context(self, context: list[Message]) -> None:
        """Restore LLM conversation context after team resume.

        No-op in the base class. Overridden by LLM-capable agents
        (e.g., BaseAgent in akgentic-agent) to restore ReactAgent context.

        Args:
            context: List of LLM message objects to restore.
        """

    ##
    ## Proxy helpers
    ##
    def proxy_tell(
        self,
        actor: ActorAddress,
        actor_type: type[AkgentType] | None = None,
    ) -> AkgentType:
        """Create a typed proxy that supports tell patterns.

        Fire-and-forget message sending without blocking for results.

        Args:
            actor: Target actor address.
            actor_type: Type hint for return typing (not used at runtime).

        Returns:
            ProxyWrapper configured for tell mode, typed as AkgentType.
        """
        proxy_instance = ProxyWrapper(actor, ask_mode=False)
        return cast(AkgentType, proxy_instance)

    def proxy_ask(
        self,
        actor: ActorAddress,
        actor_type: type[AkgentType] | None = None,
        timeout: int | None = None,
    ) -> AkgentType:
        """Create a typed proxy that supports ask patterns.

        Blocking message sending that waits for and returns results.

        Args:
            actor: Target actor address.
            actor_type: Type hint for return typing (not used at runtime).
            timeout: Optional timeout in seconds for ask operations.

        Returns:
            ProxyWrapper configured for ask mode, typed as AkgentType.
        """
        proxy_instance = ProxyWrapper(actor, ask_mode=True, timeout=timeout)
        return cast(AkgentType, proxy_instance)

    ##
    ## Agent team management
    ##
    def get_team(self) -> list[ActorAddress]:
        """Get the list of all team members from orchestrator.

        Queries orchestrator for current team roster and updates local cache
        with any new members. Each member is cached by both name and string
        representation of address.

        Returns:
            list[ActorAddress]: List of ActorAddress objects for all team members.

        raise:
            WarningError if orchestrator is not available.
        """
        if not self._orchestrator:
            raise WarningError("No orchestrator available to get team information.")

        return self.orchestrator_proxy_ask.get_team()

    def get_team_member(self, name: str) -> ActorAddress | None:
        """Get team member address by name.

        Args:
            name: Name of the team member (e.g., '@Developer123').

        Returns:
            ActorAddress | None: ActorAddress if found, None otherwise.

        raise:
            WarningError if orchestrator is not available.
        """
        if not self._orchestrator:
            raise WarningError("No orchestrator available to get team information.")

        return self.orchestrator_proxy_ask.get_team_member(name)

    ##
    ## Agent profile catalog discovery
    ##
    def discover_catalog(self) -> list[AgentCard]:
        """Browse the team's capability catalog.

        Returns:
            List of all available agent profiles (AgentCards)
        """
        if not self._orchestrator:
            return []

        return self.orchestrator_proxy_ask.get_agent_catalog()

    def get_agent_card(self, role: str) -> AgentCard | None:
        """Look up a specific agent profile by role.

        Args:
            role: Role to search for (e.g., "ResearchAgent")

        Returns:
            AgentCard if profile exists, None otherwise
        """
        if not self._orchestrator:
            return None

        return self.orchestrator_proxy_ask.get_agent_profile(role)

    def find_agents_with_skill(self, skill: str) -> list[AgentCard]:
        """Find agent profiles that have a specific skill.

        Args:
            skill: Skill to search for (e.g., "web_search")

        Returns:
            List of matching AgentCards
        """
        if not self._orchestrator:
            return []

        return self.orchestrator_proxy_ask.get_profiles_by_skill(skill)

    def get_available_roles(self) -> list[str]:
        """Get list of all available roles in the team catalog.

        Returns:
            List of role names from registered agent profiles
        """
        if not self._orchestrator:
            return []

        return self.orchestrator_proxy_ask.get_available_roles()
