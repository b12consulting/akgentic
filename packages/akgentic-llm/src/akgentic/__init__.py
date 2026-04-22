# Namespace package - allows akgentic.core, akgentic.llm, akgentic.team to coexist
__path__ = __import__("pkgutil").extend_path(__path__, __name__)
