"""Tool-layer exceptions for error signaling.

These exceptions are framework-agnostic — the integration layer (e.g., akgentic-team)
translates them into the appropriate retry mechanism (e.g., pydantic-ai ModelRetry).
"""


class RetriableError(Exception):
    """Raised by tool implementations when the LLM should retry with corrected input.

    Tools raise this to signal a recoverable error. The consuming framework
    (via ToolFactory's retry_exception injection) converts it to the appropriate
    retry mechanism.
    """

    pass
