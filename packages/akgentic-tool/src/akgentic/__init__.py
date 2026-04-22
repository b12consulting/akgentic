"""Akgentic namespace package.

This __init__.py file marks the akgentic directory as a namespace package,
allowing multiple packages to contribute to the akgentic namespace.

Do not add any code here. Each module (core, llm, team, tool, etc.) will
have its own subpackage that extends this namespace.
"""

__path__ = __import__("pkgutil").extend_path(__path__, __name__)
