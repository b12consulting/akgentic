"""Accounting Customer Support Team

Demonstrates a multi-agent accounting support team where a Manager
orchestrates specialists to resolve customer cases.

**Team composition (hire_at_start):**
- Manager — always created, routes to all specialists
- Customer Support Analyst — hired at start
- Resolution Strategist — hired at start

**Available on demand (hired dynamically by the Manager):**
- Accounting Data Specialist
- Tax Knowledge Specialist
- Email Drafting Specialist
- Quality & Compliance Reviewer

Usage:
    python src/accounting_team/main.py
"""

import logging
import time

import logfire
from agents import (
    agent_cards,
    customer_support_analyst_card,
    manager_card,
    resolution_strategist_card,
)
from akgentic.agent import AgentMessage, BaseAgent, HumanProxy
from akgentic.core import ActorSystem, BaseConfig, EventSubscriber, Orchestrator
from akgentic.core.messages import Message
from akgentic.core.messages.orchestrator import EventMessage, SentMessage
from akgentic.tool import ToolCallEvent

logging.basicConfig(level=logging.ERROR, format="%(name)s - %(levelname)s - %(message)s")


class MessagePrinter(EventSubscriber):
    """Prints messages as they flow through the orchestrator.

    Subscribes to orchestrator events and prints relevant message traffic
    to provide visibility into agent interactions.

    V1 equivalent: Controller with PrintModule
    """

    def on_stop_request(self) -> None:
        pass

    def on_message(self, message: Message) -> None:
        """Handle sentMessage events from orchestrator.

        Args:
            sender: Address of the message sender
            recipient: Address of the message recipient
            message: The message being sent
        """
        assert message.sender is not None
        sender = message.sender.name

        if isinstance(message, SentMessage):
            self.handle_sent_message(message, sender)

        if isinstance(message, EventMessage) and isinstance(message.event, ToolCallEvent):
            self.handle_tool_call_event(message, sender)

    def handle_tool_call_event(self, message: EventMessage, sender: str) -> None:
        assert isinstance(message.event, ToolCallEvent)
        event = message.event
        print("-" * 100)
        print(f"[{sender}] -> {event.tool_name} - {event.args} - {event.kwargs}\n")

    def handle_sent_message(self, message: SentMessage, sender: str) -> None:
        msg = message.message
        recipient = message.recipient.name

        # Print any message that has a content attribute
        if hasattr(msg, "content"):
            content = getattr(msg, "content", "")
            print("-" * 100)
            print(f"[{sender}] -> {msg.__class__.__name__} ({recipient}): \n{content}\n")


def main() -> None:
    """Run the simple team example with v2 APIs."""

    ## The logfire traces will now appear in the Logfire web UI as long as
    ## you have the LOGFIRE_TOKEN environment variable set.
    logfire.configure(console=False)

    # 1. Create ActorSystem
    actor_system = ActorSystem()

    # 2. Create Orchestrator
    orchestrator_addr = actor_system.createActor(
        Orchestrator, config=BaseConfig(name="@Orchestrator", role="Orchestrator")
    )
    orchestrator_proxy = actor_system.proxy_ask(orchestrator_addr, Orchestrator)

    # 3. Subscribe to orchestrator events
    printer = MessagePrinter()
    orchestrator_proxy.subscribe(printer)

    # 4. Register agent cards with orchestrator
    orchestrator_proxy.register_agent_profiles(agent_cards)

    # 5. Create HumanProxy
    human_config = BaseConfig(name="@Human", role="Human")
    human_addr = orchestrator_proxy.createActor(HumanProxy, config=human_config)
    human_proxy = actor_system.proxy_tell(human_addr, HumanProxy)

    # 6. Create Manager agent
    manager_addr = orchestrator_proxy.createActor(BaseAgent, config=manager_card.get_config_copy())
    manager_proxy = actor_system.proxy_ask(manager_addr, BaseAgent)

    # 7. Hire agents with hire_at_start=true
    manager_proxy.createActor(BaseAgent, config=customer_support_analyst_card.get_config_copy())
    manager_proxy.createActor(BaseAgent, config=resolution_strategist_card.get_config_copy())

    # 8. Wait for actors to initialize
    time.sleep(0.3)

    print()
    print(manager_proxy.cmd_get_team_roster())

    # 9. Interactive chat loop
    def print_help() -> None:
        print("Available commands:")
        print("  /exit        - Exit the chat loop")
        print("  /team        - Show team roster")
        print("  /roles       - Show available roles")
        print("  /planning    - Show current team planning")
        print("  /task <id>   - Show a specific task")
        print("  /hire <role> - Hire a new team member")
        print("  /fire <name> - Fire a team member")
        print("  /help        - Show this help message")
        print()

    print(
        "\nType your message (start the message with @{agent_name} to route to specific agent, "
        "'exit' to quit or '/help' for help):"
    )

    print("-" * 100)
    while True:
        user_input = input("")
        print()

        if user_input.strip().lower() in ["exit", "/exit"]:
            print("Exiting chat loop.")
            break

        if user_input.strip() == "":
            continue

        if user_input.startswith("/"):
            # Handle as command (e.g., /team to show team roster)
            parts = user_input.split(" ", 1)
            command = parts[0][1:]
            if command == "team":
                print(manager_proxy.cmd_get_team_roster())
                print()
            elif command == "roles":
                print(manager_proxy.cmd_get_role_profiles())
                print()
            elif command == "planning":
                print(manager_proxy.cmd_get_planning())
                print()
            elif command == "task" and len(parts) > 1:
                task_id = int(parts[1].strip())
                result = manager_proxy.cmd_get_planning_task(task_id)
                print(result)
                print()
            elif command == "hire" and len(parts) > 1:
                role = parts[1].strip()
                result = manager_proxy.cmd_hire_member(role)
                if isinstance(result, tuple):
                    name, addr = result
                    print(f"Hired {name} ({role}) at {addr}\n")
                elif isinstance(result, str):
                    print(f"Hire failed: {result}\n")
            elif command == "fire" and len(parts) > 1:
                name = parts[1].strip()
                result = manager_proxy.cmd_fire_member(name)
                print(result)
                print()
            else:
                print_help()
            continue

        if user_input.startswith("@"):
            # Route to specific agent
            target_name = user_input.split(" ")[0]
            actor_addr = orchestrator_proxy.get_team_member(target_name)
            if actor_addr is None:
                print(f"Error: Agent {target_name} not found")
                continue
            human_proxy.send(actor_addr, AgentMessage(content=user_input))
            continue

        human_proxy.send(manager_addr, AgentMessage(content=user_input))

    # 10. Shutdown
    actor_system.shutdown()


if __name__ == "__main__":
    main()
