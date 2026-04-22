# Namespace package - allows akgentic.core, akgentic.llm, akgentic.agent to coexist
from __future__ import annotations

__path__ = __import__("pkgutil").extend_path(__path__, __name__)
