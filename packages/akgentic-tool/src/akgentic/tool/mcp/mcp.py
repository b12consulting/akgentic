"""Generic MCP protocol support for akgentic-tool.

This module contains protocol-level concerns only:
- MCP transport configuration
- MCP server toolset creation
- MCP diagnostics (tool listing)
"""

from __future__ import annotations

from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from akgentic.tool.core import ToolCard

MCPHTTPTransport = Literal["streamable-http", "sse"]


class MCPHTTPConnectionConfig(BaseModel):
    """HTTP/SSE transport configuration for an MCP endpoint."""

    url: str = Field(description="MCP endpoint URL for HTTP/SSE transports")
    transport: MCPHTTPTransport = Field(
        default="streamable-http",
        description="MCP HTTP transport type",
    )
    bearer_token: str | None = Field(
        default=None,
        description="Optional bearer token for Authorization header",
    )
    timeout: float = Field(
        default=10.0,
        gt=0,
        description="Connection initialization timeout for MCP server",
    )
    read_timeout: float = Field(
        default=300.0,
        gt=0,
        description="Read timeout for MCP transport",
    )
    tool_prefix: str | None = Field(
        default=None,
        description="Optional tool name prefix applied by pydantic-ai MCP wrapper",
    )


class MCPStdioConnectionConfig(BaseModel):
    """stdio transport configuration for an MCP server subprocess."""

    transport: Literal["stdio"] = Field(
        default="stdio",
        description="MCP stdio transport",
    )
    stdio_command: str | None = Field(
        default=None,
        description="Command to launch MCP server in stdio mode (e.g., docker, npx, uvx)",
    )
    stdio_args: list[str] = Field(
        default_factory=list,
        description="Arguments for stdio command",
    )
    stdio_env: dict[str, str] | None = Field(
        default=None,
        description="Environment variables passed to stdio MCP process",
    )
    stdio_cwd: str | None = Field(
        default=None,
        description="Working directory for stdio MCP process",
    )
    stdio_token_env_var: str | None = Field(
        default=None,
        description="If set with bearer_token, inject token into this env var for stdio process",
    )
    bearer_token: str | None = Field(
        default=None,
        description="Optional token that can be injected into stdio env via stdio_token_env_var",
    )
    timeout: float = Field(
        default=10.0,
        gt=0,
        description="Connection initialization timeout for MCP server",
    )
    read_timeout: float = Field(
        default=300.0,
        gt=0,
        description="Read timeout for MCP transport",
    )
    tool_prefix: str | None = Field(
        default=None,
        description="Optional tool name prefix applied by pydantic-ai MCP wrapper",
    )


MCPConnectionConfig = MCPHTTPConnectionConfig | MCPStdioConnectionConfig
"""Union type for MCP connection configurations.

Supports both HTTP/SSE and stdio transport types.
"""


class MCPDiagnosticsConfig(BaseModel):
    """Generic diagnostics behavior for MCP probing."""

    max_tools_to_print: int = Field(default=20, ge=1)


def _mcp_auth_headers(bearer_token: str | None) -> dict[str, str] | None:
    """Build HTTP authorization headers from a bearer token.

    Args:
        bearer_token: Optional bearer token for authentication.

    Returns:
        Dictionary with Authorization header if token provided, None otherwise.
    """
    if not bearer_token:
        return None
    return {"Authorization": f"Bearer {bearer_token}"}


def _load_mcp_server_classes() -> tuple[type[Any], type[Any], type[Any]]:
    """Lazy-load MCP server classes from pydantic-ai.

    Returns:
        Tuple of (MCPServerSSE, MCPServerStreamableHTTP, MCPServerStdio) classes.

    Raises:
        ImportError: If pydantic-ai MCP extras are not installed.
    """
    try:
        from pydantic_ai.mcp import (  # noqa: PLC0415
            MCPServerSSE,
            MCPServerStdio,
            MCPServerStreamableHTTP,
        )
    except ImportError as error:  # pragma: no cover - environment-specific
        raise ImportError(
            "MCP support requires pydantic-ai MCP extras. "
            'Install with: pip install "pydantic-ai-slim[mcp]"'
        ) from error

    return MCPServerSSE, MCPServerStreamableHTTP, MCPServerStdio


class MCPTool(ToolCard):
    """MCP protocol integration — exposes tools via toolsets, not callables.

    Attributes:
        connection: MCP transport configuration (HTTP/SSE or stdio).
    """

    connection: MCPConnectionConfig

    def get_tools(self) -> list[Callable]:
        """MCP tools come via toolsets, not individual callables."""
        return []

    def get_toolsets(self) -> list[Any]:
        """Create and return an MCP server toolset for pydantic-ai agents.

        Creates the appropriate MCP server instance based on connection configuration:
        - MCPServerStdio for stdio transport
        - MCPServerSSE for SSE transport
        - MCPServerStreamableHTTP for streamable-http transport

        Returns:
            List containing a single configured MCP server instance ready to be
            used as a toolset in pydantic-ai agents.

        Raises:
            ValueError: If stdio_command is missing for stdio transport.
            ImportError: If pydantic-ai MCP extras are not installed.
        """
        mcp_server_sse, mcp_server_streamable_http, mcp_server_stdio = _load_mcp_server_classes()
        headers = _mcp_auth_headers(self.connection.bearer_token)

        if isinstance(self.connection, MCPStdioConnectionConfig):
            if not self.connection.stdio_command:
                raise ValueError("stdio_command is required for MCPStdioConnectionConfig")

            env = dict(self.connection.stdio_env or {})
            if self.connection.stdio_token_env_var and self.connection.bearer_token:
                env[self.connection.stdio_token_env_var] = self.connection.bearer_token

            return [
                mcp_server_stdio(
                    command=self.connection.stdio_command,
                    args=self.connection.stdio_args,
                    env=env or None,
                    cwd=self.connection.stdio_cwd,
                    tool_prefix=self.connection.tool_prefix,
                    timeout=self.connection.timeout,
                    read_timeout=self.connection.read_timeout,
                )
            ]

        if self.connection.transport == "sse":
            return [
                mcp_server_sse(
                    url=self.connection.url,
                    headers=headers,
                    tool_prefix=self.connection.tool_prefix,
                    timeout=self.connection.timeout,
                    read_timeout=self.connection.read_timeout,
                )
            ]

        return [
            mcp_server_streamable_http(
                url=self.connection.url,
                headers=headers,
                tool_prefix=self.connection.tool_prefix,
                timeout=self.connection.timeout,
                read_timeout=self.connection.read_timeout,
            )
        ]


async def list_mcp_tools(connection: MCPConnectionConfig) -> list[str]:
    """Connect to an MCP server and return exposed tool names.

    Args:
        connection: MCP connection configuration (HTTP/SSE or stdio).

    Returns:
        List of tool names exposed by the MCP server.

    Raises:
        ImportError: If pydantic-ai MCP extras are not installed.
        Exception: If connection fails or server is unreachable.
    """
    tool = MCPTool(
        name="mcp-probe",
        description="Probe MCP endpoint",
        connection=connection,
    )
    print("## Creating MCP toolset for diagnostics...")
    toolsets = tool.get_toolsets()
    if not toolsets:
        raise ValueError("MCPTool.get_toolsets() returned empty list")
    server = toolsets[0]
    print("## Server toolset created, connecting and listing tools...")
    async with server:
        print("## Connected to MCP server, fetching tool list...")
        tools = await server.list_tools()
    return [tool_def.name for tool_def in tools]


async def probe_mcp_connection(
    connection: MCPConnectionConfig,
    *,
    max_tools_to_print: int = 20,
) -> dict[str, Any]:
    """Probe an MCP server and return a compact feasibility summary.

    Args:
        connection: MCP connection configuration to probe.
        max_tools_to_print: Maximum number of tool names to include in result.

    Returns:
        Dictionary with tool_count, tools (list), and feasible (bool).
    """
    tool_names = await list_mcp_tools(connection)
    return {
        "tool_count": len(tool_names),
        "tools": tool_names[:max_tools_to_print],
        "feasible": len(tool_names) > 0,
    }
