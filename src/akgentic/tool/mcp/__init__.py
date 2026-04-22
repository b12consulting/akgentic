"""MCP support for akgentic-tool."""

from .mcp import (
    MCPConnectionConfig,
    MCPDiagnosticsConfig,
    MCPHTTPConnectionConfig,
    MCPStdioConnectionConfig,
    MCPTool,
    list_mcp_tools,
    probe_mcp_connection,
)
from .oauth_handler import (
    get_mcp_token_with_oauth_if_needed,
    handle_mcp_oauth_flow,
    parse_www_authenticate_header,
    probe_mcp_with_oauth,
)

__all__ = [
    "MCPConnectionConfig",
    "MCPDiagnosticsConfig",
    "MCPHTTPConnectionConfig",
    "MCPStdioConnectionConfig",
    "MCPTool",
    "get_mcp_token_with_oauth_if_needed",
    "handle_mcp_oauth_flow",
    "list_mcp_tools",
    "parse_www_authenticate_header",
    "probe_mcp_connection",
    "probe_mcp_with_oauth",
]
