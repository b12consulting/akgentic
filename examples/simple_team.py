"""Agent Team Example — Single-File Demo

Demonstrates the core capabilities of akgentic-agent:

- Multi-agent team coordination with Manager, Assistant, and Expert roles
- AgentCard-based role definitions with dynamic hiring via TeamTool
- Asynchronous message flow: AgentMessage → StructuredOutput → routing
- Interactive chat loop with /commands, @mention routing, and planning
- EventSubscriber for real-time message and tool-call visibility

Team structure:
    @Human (HumanProxy)
        └──→ @Manager (BaseAgent, routes_to=[Assistant, Expert])
                  ├── @Assistant (BaseAgent)
                  └── @Expert (BaseAgent)

Run: uv run python packages/akgentic-agent/examples/simple_team.py
"""

import logging
import time
from collections import defaultdict

from akgentic.core import ActorSystem, AgentCard, BaseConfig, EventSubscriber, Orchestrator
from akgentic.core.messages import Message
from akgentic.core.messages.orchestrator import EventMessage, SentMessage
from akgentic.llm import (
    LlmUsageEvent,
    ModelConfig,
    PromptTemplate,
    ToolCallEvent,
    aggregate_usage,
)
from akgentic.llm.config import RuntimeConfig, UsageLimits
from akgentic.tool.planning import PlanningTool, UpdatePlanning
from akgentic.tool.search import SearchTool, WebCrawl, WebFetch, WebSearch
from akgentic.tool.workspace import WorkspaceTool

from akgentic.agent import AgentConfig, AgentMessage, BaseAgent, HumanProxy

logging.basicConfig(level=logging.ERROR, format="%(name)s - %(levelname)s - %(message)s")

# =============================================================================
# LLM MODEL — change to match your available model
# =============================================================================

LLM_MODEL = "gpt-4.1"

# =============================================================================
# TOOLS
# =============================================================================

# PlanningTool — shared task tracking across the team
planning_tool = PlanningTool(
    update_planning=UpdatePlanning(
        instructions=(
            "Always keep the plan updated. "
            "Create tasks when your work involves other team members or multiple steps. "
            "Update task status as you make progress. "
            "Do not finish your turn if the plan is stale."
        )
    )
)

# SearchTool — web search, fetch, and crawl via Tavily (requires TAVILY_API_KEY)
search_tool = SearchTool(
    web_search=WebSearch(max_results=3),
    web_crawl=WebCrawl(timeout=150, max_depth=3, max_breadth=5, limit=10),
    web_fetch=WebFetch(timeout=30),
)

# WorkspaceTool — file I/O anchored to a named team workspace
WORKSPACE_ID = "agent-team"
workspace_tool = WorkspaceTool(workspace_id=WORKSPACE_ID)

tools = [search_tool, planning_tool, workspace_tool]

# =============================================================================
# AGENT CARDS — declarative role definitions registered with the Orchestrator.
# Cards act as blueprints: the Orchestrator stores them so agents can hire roles
# dynamically at runtime without hard-coding agent addresses.
# =============================================================================

manager_card = AgentCard(
    role="Manager",
    description="Coordinates the team and delegates tasks to Assistant and Expert",
    skills=["coordination", "delegation", "planning"],
    agent_class="akgentic.agent.BaseAgent",
    config=AgentConfig(
        name="@Manager",
        role="Manager",
        prompt=PromptTemplate(
            template=(
                "You are a helpful project manager. "
                "Coordinate team work effectively and delegate to specialists when needed."
            )
        ),
        model_cfg=ModelConfig(provider="openai", model=LLM_MODEL, temperature=0.3),
        usage_limits=UsageLimits(request_limit=20, total_tokens_limit=200_000),
        runtime_cfg=RuntimeConfig(),
        tools=tools,
    ),
    routes_to=["Assistant", "Expert"],  # roles this agent can hire on demand
)

assistant_card = AgentCard(
    role="Assistant",
    description="Provides support, research, and clear written answers",
    skills=["research", "writing", "communication"],
    agent_class="akgentic.agent.BaseAgent",
    config=AgentConfig(
        name="@Assistant",
        role="Assistant",
        prompt=PromptTemplate(
            template="You are a helpful assistant. Provide clear and accurate information."
        ),
        model_cfg=ModelConfig(provider="openai", model=LLM_MODEL, temperature=0.3),
        tools=tools,
    ),
)

expert_card = AgentCard(
    role="Expert",
    description="Provides deep specialized knowledge and technical analysis",
    skills=["analysis", "problem-solving", "domain expertise"],
    agent_class="akgentic.agent.BaseAgent",
    config=AgentConfig(
        name="@Expert",
        role="Expert",
        prompt=PromptTemplate(
            template="You are a domain expert. Provide deep, specialized knowledge and analysis."
        ),
        model_cfg=ModelConfig(provider="openai", model=LLM_MODEL, temperature=0.3),
        tools=tools,
    ),
)

agent_cards = [manager_card, assistant_card, expert_card]

# =============================================================================
# EVENT SUBSCRIBER — prints messages and tool calls as they flow through the
# orchestrator, giving visibility into agent interactions without coupling
# agents to each other.
# =============================================================================


class MessagePrinter(EventSubscriber):
    """Prints AgentMessages and tool-call events from the orchestrator.

    Collects LlmUsageEvent per agent for on-demand cost reporting via /usage.
    """

    EXCLUDED_ROLES = {"Orchestrator", "Human", "human"}

    def __init__(self) -> None:
        self._usage_events: dict[str, list[LlmUsageEvent]] = defaultdict(list)

    def on_stop_request(self) -> None:
        pass

    def on_stop(self) -> None:
        pass

    def set_restoring(self, restoring: bool) -> None:
        pass

    def on_message(self, message: Message) -> None:
        assert message.sender is not None
        sender = message.sender.name

        if isinstance(message, SentMessage):
            self.handle_sent_message(message, sender)
        elif isinstance(message, EventMessage):
            if isinstance(message.event, ToolCallEvent):
                self.handle_tool_call_event(message, sender)
            elif isinstance(message.event, LlmUsageEvent):
                if message.sender.role not in self.EXCLUDED_ROLES:
                    self._usage_events[sender].append(message.event)

    def handle_tool_call_event(self, message: EventMessage, sender: str) -> None:
        assert isinstance(message.event, ToolCallEvent)
        event = message.event
        print("-" * 100)
        print(f"[{sender}] -> {event.tool_name} - {event.arguments}\n")

    def handle_sent_message(self, message: SentMessage, sender: str) -> None:
        msg = message.message
        recipient = message.recipient.name

        if hasattr(msg, "content"):
            message_name = msg.__class__.__name__
            content = getattr(msg, "content", "")
            type = getattr(msg, "type", "")
            print("-" * 100)
            print(f"[{sender}] -> {message_name}({type}) [{recipient}]: \n{content}\n")

    @staticmethod
    def _fmt_tokens(n: int) -> str:
        """Format token count with European thousands separator (dot)."""
        return f"{n:,}".replace(",", ".")

    @staticmethod
    def _fmt_cost(c: float) -> str:
        """Format cost in European style (comma decimal). Show — when zero."""
        if c == 0.0:
            return "       —"
        formatted = f"{c:,.4f}".replace(",", " ").replace(".", ",").replace(" ", ".")
        return f"${formatted}"

    def get_usage_report(self) -> str:
        """Build a usage report across all tracked agents using aggregate_usage()."""
        lines = [
            "\n  LLM Usage Report",
            "  " + "=" * 80,
            f"  {'Agent':<20} {'in':>10}  {'out':>10}  {'reqs':>4}  {'cost':>10}",
            "  " + "-" * 80,
        ]

        total_input = 0
        total_output = 0
        total_cost = 0.0
        total_requests = 0

        for name in sorted(self._usage_events):
            summary = aggregate_usage(self._usage_events[name])
            total_input += summary.total_input_tokens
            total_output += summary.total_output_tokens
            total_cost += summary.total_cost_usd
            total_requests += summary.total_requests
            lines.append(
                f"  {name:<20} "
                f"{self._fmt_tokens(summary.total_input_tokens):>10}  "
                f"{self._fmt_tokens(summary.total_output_tokens):>10}  "
                f"{summary.total_requests:>4}  "
                f"{self._fmt_cost(summary.total_cost_usd):>10}"
            )

        lines.append("  " + "-" * 80)
        lines.append(
            f"  {'TOTAL':<20} "
            f"{self._fmt_tokens(total_input):>10}  "
            f"{self._fmt_tokens(total_output):>10}  "
            f"{total_requests:>4}  "
            f"{self._fmt_cost(total_cost):>10}"
        )
        return "\n".join(lines)


# =============================================================================
# MAIN
# =============================================================================


def main() -> None:
    """Start the agent team and run the interactive chat loop."""

    # 1. ActorSystem — Pykka-based actor runtime, the foundation for all agents
    actor_system = ActorSystem()

    # 2. Orchestrator — tracks team state, routes events to subscribers, and
    #    manages actor lifecycle. All agents register themselves via it.
    orchestrator_addr = actor_system.createActor(
        Orchestrator, config=BaseConfig(name="@Orchestrator", role="Orchestrator")
    )
    orchestrator_proxy = actor_system.proxy_ask(orchestrator_addr, Orchestrator)

    # 3. Subscribe to orchestrator events for real-time message visibility
    printer = MessagePrinter()
    orchestrator_proxy.subscribe(printer)

    # 4. Register AgentCards — defines the roles available for dynamic hiring
    orchestrator_proxy.register_agent_profiles(agent_cards)

    # 5. HumanProxy — the human-in-the-loop entry point. Receives AgentMessages
    #    from agents and routes human input back into the actor system.
    human_addr = orchestrator_proxy.createActor(
        HumanProxy, config=BaseConfig(name="@Human", role="Human")
    )

    # 6. Instantiate Manager — the team entry point, pre-hired at startup
    manager_addr = orchestrator_proxy.createActor(
        BaseAgent, config=manager_card.get_config_copy()
    )
    manager_proxy = actor_system.proxy_ask(manager_addr, BaseAgent)

    # 7. Pre-hire Assistant and Expert under Manager (Manager could also hire
    #    them on demand via its TeamTool when it first needs them)
    manager_proxy.createActor(BaseAgent, config=assistant_card.get_config_copy())
    manager_proxy.createActor(BaseAgent, config=expert_card.get_config_copy())

    # 8. Wait for Pykka actors to initialize
    time.sleep(0.3)

    print()
    print(manager_proxy.cmd_get_team_roster())

    # 9. Interactive chat loop
    def print_help() -> None:
        print("Available commands:")
        print("  /exit           Exit the chat loop")
        print("  /team           Show current team roster")
        print("  /roles          Show available roles for hiring")
        print("  /planning       Show current team planning")
        print("  /task <id>      Show a specific planning task by ID")
        print("  /hire <role>    Hire a new team member by role")
        print("  /fire <name>    Fire a team member by name")
        print("  /usage          Show LLM usage and cost per agent")
        print("  /help           Show this help message")
        print()

    print(
        "\nType your message (use @AgentName to route to a specific agent, "
        "/help for commands, /exit to quit):"
    )
    print("-" * 100)

    while True:
        user_input = input("").strip()
        print()

        if not user_input:
            continue

        if user_input.lower() in ["exit", "/exit"]:
            print("Exiting chat loop.")
            break

        if user_input.startswith("/"):
            parts = user_input.split(" ", 1)
            command = parts[0][1:]
            arg = parts[1].strip() if len(parts) > 1 else ""

            if command == "team":
                print(manager_proxy.cmd_get_team_roster())
            elif command == "roles":
                print(manager_proxy.cmd_get_role_profiles())
            elif command == "planning":
                print(manager_proxy.cmd_get_planning())
            elif command == "task" and arg:
                print(manager_proxy.cmd_get_planning_task(int(arg)))
            elif command == "hire" and arg:
                result = manager_proxy.cmd_hire_member(arg)
                if isinstance(result, tuple):
                    name, addr = result
                    print(f"Hired {name} ({arg}) at {addr}")
                else:
                    print(f"Hire failed: {result}")
            elif command == "fire" and arg:
                print(manager_proxy.cmd_fire_member(arg))
            elif command == "usage":
                print(printer.get_usage_report())
            else:
                print_help()
            print()
            continue

        # @mention routing — send directly to a named agent
        if user_input.startswith("@"):
            target_name = user_input.split(" ")[0]
            actor_addr = orchestrator_proxy.get_team_member(target_name)
            if actor_addr is None:
                print(f"Agent {target_name} not found. Use /team to list available agents.\n")
                continue
            human_addr.send(actor_addr, AgentMessage(content=user_input))
        else:
            # Default: send to Manager for coordination
            human_addr.send(manager_addr, AgentMessage(content=user_input))

    # 10. Graceful shutdown — stops all Pykka actors
    actor_system.shutdown()


if __name__ == "__main__":
    main()
