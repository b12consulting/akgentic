"""akgentic-agent: Collaborative agent patterns for the Akgentic framework.

Continuation-based request/answer flows, dynamic team composition,
hierarchical agent management, and structured output patterns.

Public API will be exported here as modules are implemented.
"""

from akgentic.agent.agent import BaseAgent
from akgentic.agent.config import AgentConfig
from akgentic.agent.human_proxy import HumanProxy
from akgentic.agent.messages import AgentMessage

__version__ = "0.1.0"

__all__ = [
    # Version
    "__version__",
    "AgentConfig",
    # Human-in-the-loop
    "HumanProxy",
    # BaseAgent
    "BaseAgent",
    # Team messages and continuation
    "AgentMessage",
]
