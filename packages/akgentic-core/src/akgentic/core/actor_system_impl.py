"""Actor system implementation using Pykka for local in-memory actor runtime.

This module provides the concrete implementation of the actor system without
protocol abstraction (following YAGNI - protocol will be extracted later when
multiple implementations exist).
"""

from __future__ import annotations

import logging
import uuid
from collections import deque
from collections.abc import Generator
from contextlib import contextmanager
from time import sleep
from typing import Any, TypeVar, cast

import pykka
from pydantic import BaseModel

from akgentic.core.actor_address import ActorAddress
from akgentic.core.actor_address_impl import ActorAddressImpl
from akgentic.core.agent import Akgent
from akgentic.core.agent_config import BaseConfig
from akgentic.core.messages.message import Message
from akgentic.core.utils.deserializer import import_class

# Logger for agent operations
logger = logging.getLogger(__name__)

T = TypeVar("T")


class Statistics(BaseModel):
    """System statistics for monitoring and debugging.

    Attributes:
        orchestrator_count: Number of active orchestrators in the system.
        agent_count: Number of active agents (excluding orchestrators).
    """

    orchestrator_count: int = 0
    agent_count: int = 0


class ActorSystemListener(pykka.ThreadingActor):
    """Internal listener actor for receiving messages in execution contexts.

    This actor maintains a message queue and waiter queue to handle asynchronous
    message delivery using Pykka futures.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the listener actor with message and waiter queues."""
        super().__init__(*args, **kwargs)
        self._message_queue: deque[Any] = deque()
        self._waiters: deque[pykka.ThreadingFuture[Any]] = deque()
        self.agent_id = uuid.uuid4()

    def myAddress(self) -> ActorAddressImpl:  # noqa: N802
        """Get the address of this listener actor.

        Returns:
            ActorAddressImpl: The address wrapping this actor's reference.
        """
        return ActorAddressImpl(self.actor_ref)

    def on_receive(self, message: Any) -> Any:
        """Handle incoming messages by queuing or resolving waiters.

        Args:
            message: The message to process.

        Returns:
            None. Messages are either queued or delivered to waiting futures.
        """
        if self._waiters:
            fut = self._waiters.popleft()
            try:
                fut.set(message)
            except Exception:
                self._message_queue.append(message)
        else:
            self._message_queue.append(message)

    def listen(self) -> Any:
        """Listen for the next message, blocking if none available.

        Returns:
            Either a message from the queue or a future that will resolve
            when a message arrives.
        """
        if self._message_queue:
            return self._message_queue.popleft()
        fut: pykka.ThreadingFuture[Any] = pykka.ThreadingFuture()
        self._waiters.append(fut)
        return fut


class ExecutionContext:
    """Execution context for sending and receiving messages to/from actors.

    Each context has its own listener actor that can receive replies. Contexts
    can be used for temporary message exchanges without creating full actors.
    """

    def __init__(self) -> None:
        """Initialize execution context with a listener actor."""
        self.listener_ref: pykka.ActorRef[ActorSystemListener] = ActorSystemListener.start()

    @property
    def myAddress(self) -> ActorAddressImpl:  # noqa: N802
        """Get the address of this context's listener actor.

        Returns:
            ActorAddressImpl: The address for this context.
        """
        return ActorAddressImpl(self.listener_ref)

    def tell(self, actor: ActorAddress, message: Any) -> None:
        """Send a fire-and-forget message to an actor.

        Args:
            actor: The target actor address.
            message: The message to send.
        """
        recipient = cast(ActorAddressImpl, actor)
        if isinstance(message, Message):
            message.sender = ActorAddressImpl(self.listener_ref)
            message.team_id = recipient.team_id

        recipient._actor_ref.tell(message)

    def ask(self, actor: ActorAddress, message: Any, timeout: float | None = None) -> Any:
        """Send a message to an actor and wait for a response.

        Args:
            actor: The target actor address.
            message: The message to send.
            timeout: Optional timeout in seconds.

        Returns:
            The response from the actor.
        """
        recipient = cast(ActorAddressImpl, actor)
        if isinstance(message, Message):
            message.sender = ActorAddressImpl(self.listener_ref)
            message.team_id = recipient.team_id

        return recipient._actor_ref.ask(message, timeout=timeout)

    def listen(self, timeout: float | None = None) -> Any:
        """Listen for a message sent to this context's listener.

        Args:
            timeout: Optional timeout in seconds.

        Returns:
            The received message.
        """
        result = self.listener_ref.proxy().listen().get()
        if isinstance(result, pykka.Future):
            return result.get(timeout=timeout)
        return result

    def shutdown(self, timeout: int | None = None) -> None:
        """Shutdown this execution context and stop its listener actor.

        Args:
            timeout: Optional timeout in seconds for stopping the listener.
        """
        try:
            self.listener_ref.stop(timeout=timeout)
        except Exception as e:
            logger.error(f"Warning: Failed to stop listener actor: {e}")


class ActorSystem(ExecutionContext):
    """Concrete actor system implementation using Pykka for local in-memory runtime.

    This is the primary interface for creating, managing, and shutting down actors.
    It extends ExecutionContext to provide actor lifecycle management and system
    statistics.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the actor system with Pykka registry."""
        super().__init__(*args, **kwargs)
        self.ActorRegistry = pykka.ActorRegistry

    @property
    def orchestrators(self) -> list[pykka.ActorRef[Any]]:
        """Get all active orchestrator actors.

        Returns:
            List of orchestrator actor references.
        """
        return [
            orch for orch in self.ActorRegistry.get_by_class_name("Orchestrator") if orch.is_alive()
        ]

    def get_actor(self, agent: ActorAddress) -> ActorAddress | None:
        """Find an actor by its agent ID.

        Args:
            agent: Actor address containing the agent_id to search for.

        Returns:
            The actor address if found, None otherwise.
        """
        for actor in self.ActorRegistry.get_all():
            underlying = actor._actor_weakref()
            if underlying is None:
                # Actor has been garbage collected — skip silently
                continue
            if str(underlying.agent_id) == str(agent.agent_id):
                return ActorAddressImpl(actor)
        return None

    def stat(self) -> list[Statistics]:
        """Get system statistics including actor counts.

        Returns:
            List containing a single Statistics object with current counts.
        """
        stat = Statistics(
            orchestrator_count=len(self.orchestrators),
            agent_count=len(self.ActorRegistry.get_all()),
        )
        return [stat]

    def shutdown(self, timeout: int | None = 120) -> None:
        """Gracefully shutdown the actor system.

        This method:
        1. Stops the execution context listener
        2. Stops all orchestrators in parallel
        3. Cleans up any remaining actors

        Args:
            timeout: Maximum time in seconds to wait for shutdown. Defaults to 120.
        """
        super().shutdown(timeout=timeout)

        # Start all stops in parallel (non-blocking)
        stop_futures = [actor.proxy().stop() for actor in self.orchestrators]

        # Wait for all stops to complete
        for future in stop_futures:
            try:
                future.get(timeout=timeout)
            except pykka.Timeout:
                logger.warning("Warning: Timeout while stopping an actor.")
            except Exception as e:
                logger.error(f"Error: Failed to stop actor: {e}")

        # Pykka's proxy().stop() future resolves when the stop() method returns,
        # NOT when the actor is fully deregistered. Internally, stop() sends 
        # (fire-and-forget) a _ActorStop() messages to the actor's own mailbox.
        # The actor thread must still process that message, run on_stop(), and 
        # deregister from ActorRegistry. Without this pause, get_all() below may see
        # actors that have accepted the stop request but haven't finished processing it yet.
        sleep(0.2)

        # Final cleanup: force-stop any actors still in the registry
        remaining_actors = pykka.ActorRegistry.get_all()
        remaining_actors_classes = [actor.actor_class.__name__ for actor in remaining_actors]
        if remaining_actors:
            logger.warning(
                f"\nStopping remaining {len(remaining_actors)} actors: {remaining_actors_classes}"
            )
            pykka.ActorRegistry.stop_all()

    def createActor(  # noqa: N802
        self,
        actor_class: type[Akgent[Any, Any]] | str,
        restoring: bool = False,
        agent_id: uuid.UUID | str | None = None,
        user_id: str | None = None,
        user_email: str | None = None,
        team_id: uuid.UUID | str | None = None,
        config: BaseConfig | None = None,
    ) -> ActorAddress:
        """Create and start a new actor.

        Args:
            actor_class: The actor class type or fully qualified class string.
            restoring: Whether the actor is being restored from persistence.
            agent_id: Optional UUID for the agent. Generated by Agent if not provided.
            user_id: Optional user ID for the actor.
            user_email: Optional user email for the actor.
            team_id: Optional team UUID for the actor. Generated by Agent if not provided.
            config: Configuration for the actor.

        Returns:
            ActorAddress: The address of the newly created actor.

        Raises:
            ValueError: If actor_class is not a Type or string.
        """
        if config is None:
            config = BaseConfig()

        config.squad_id = config.squad_id or uuid.uuid4()

        if agent_id is None:
            agent_id = uuid.uuid4()
        elif isinstance(agent_id, str):
            agent_id = uuid.UUID(agent_id)

        if team_id is None:
            team_id = uuid.uuid4()
        elif isinstance(team_id, str):
            team_id = uuid.UUID(team_id)

        actor_type: type[Akgent[Any, Any]]
        if isinstance(actor_class, str):
            actor_type = import_class(actor_class)
        else:
            actor_type = actor_class

        # Use keyword arguments matching Agent.__init__ signature
        actor = actor_type.start(
            agent_id=agent_id,
            config=config,
            user_id=user_id,
            user_email=user_email,
            team_id=team_id,
            restoring=restoring,
        )
        actor_addr = ActorAddressImpl(actor)
        return actor_addr

    @contextmanager
    def private(self) -> Generator[ExecutionContext, None, None]:
        """Create a temporary execution context for private message exchanges.

        This context manager creates a temporary ExecutionContext with its own
        listener actor that is automatically cleaned up when the context exits.

        Yields:
            ExecutionContext: A temporary execution context.

        Example:
            >>> system = ActorSystem()
            >>> with system.private() as ctx:
            ...     ctx.tell(some_actor, "message")
        """
        try:
            execution_context = ExecutionContext()
            yield execution_context
        finally:
            try:
                execution_context.shutdown()
            except Exception as e:
                logger.error(f"Warning: Failed to stop execution context: {e}")

    def proxy_tell(self, actor: ActorAddress, actor_type: type[T]) -> T:
        """Create a typed proxy for fire-and-forget messaging.

        Args:
            actor: The target actor address.
            actor_type: The type hint for the proxy (for IDE support).

        Returns:
            A typed proxy wrapper that sends tell messages.
        """
        wrapped_proxy = ProxyWrapper(actor, ask_mode=False)
        return cast(T, wrapped_proxy)

    def proxy_ask(
        self,
        actor: ActorAddress,
        actor_type: type[T],
        timeout: float | None = None,
    ) -> T:
        """Create a typed proxy for request-response messaging.

        Args:
            actor: The target actor address.
            actor_type: The type hint for the proxy (for IDE support).
            timeout: Optional timeout in seconds for ask operations.

        Returns:
            A typed proxy wrapper that sends ask messages and waits for responses.
        """
        wrapped_proxy = ProxyWrapper(actor, ask_mode=True, timeout=timeout)
        return cast(T, wrapped_proxy)


class ProxyWrapper:
    """Wrapper around Pykka proxy for tell/ask patterns.

    This class provides a transparent proxy that intercepts method calls and
    converts them to either tell (fire-and-forget) or ask (request-response)
    messages based on the mode.
    """

    def __init__(
        self, actor: ActorAddress, ask_mode: bool = False, timeout: float | None = None
    ) -> None:
        """Initialize the proxy wrapper.

        Args:
            actor: The target actor address.
            ask_mode: If True, use ask pattern (wait for response). If False, use tell.
            timeout: Optional timeout in seconds for ask operations.
        """
        self._pykka_proxy = cast(ActorAddressImpl, actor)._actor_ref.proxy()
        self._ask_mode = ask_mode
        self._timeout = timeout

    def __getattr__(self, name: str) -> Any:
        """Intercept attribute access and wrap methods in tell/ask logic.

        Args:
            name: The attribute name being accessed.

        Returns:
            For callables: a wrapper function that performs tell or ask.
            For non-callables: the attribute value (resolved if ask_mode).
        """
        attr = getattr(self._pykka_proxy, name)

        if hasattr(attr, "__call__"):
            if self._ask_mode:

                def ask_wrapper(*args: Any, **kwargs: Any) -> Any:
                    future = attr(*args, **kwargs)
                    return future.get(timeout=self._timeout)

                return ask_wrapper
            else:

                def tell_wrapper(*args: Any, **kwargs: Any) -> None:
                    attr(*args, **kwargs)
                    return None

                return tell_wrapper

        if self._ask_mode and hasattr(attr, "get"):
            return attr.get(timeout=self._timeout)

        return attr

    def __repr__(self) -> str:
        """String representation of the proxy wrapper.

        Returns:
            A string describing this proxy wrapper.
        """
        return f"<ProxyWrapper for {self._pykka_proxy}>"
