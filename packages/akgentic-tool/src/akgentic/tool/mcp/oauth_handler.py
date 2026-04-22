"""OAuth handler for MCP servers requiring OAuth authentication.

This module implements the OAuth flow for MCP servers that return 401 Unauthorized
with authentication information in the WWW-Authenticate header.
"""

from __future__ import annotations

import asyncio
import re
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler for OAuth callback."""

    auth_code: str | None = None
    auth_state: str | None = None

    def do_GET(self) -> None:  # noqa: N802
        """Handle OAuth callback GET request."""
        parsed = urlparse(self.path)
        query_params = parse_qs(parsed.query)

        if "code" in query_params:
            OAuthCallbackHandler.auth_code = query_params["code"][0]
            if "state" in query_params:
                OAuthCallbackHandler.auth_state = query_params["state"][0]

            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h1>Authentication successful!</h1>"
                b"<p>You can close this window and return to your application.</p></body></html>"
            )
        else:
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h1>Authentication failed!</h1>"
                b"<p>No authorization code received.</p></body></html>"
            )

    def log_message(self, format: str, *args: Any) -> None:  # noqa: ARG002, A002
        """Suppress log messages."""


def parse_www_authenticate_header(header_value: str) -> dict[str, str]:
    """Parse WWW-Authenticate header for MCP OAuth information.

    Expected format: Model Context Protocol auth_url="<url>" [param="value" ...]

    Args:
        header_value: WWW-Authenticate header value

    Returns:
        Dictionary with parsed parameters (auth_url, etc.)

    Example:
        >>> header = 'Model Context Protocol auth_url="https://auth.example.com/oauth"'
        >>> parse_www_authenticate_header(header)
        {'auth_url': 'https://auth.example.com/oauth'}
    """
    result: dict[str, str] = {}

    # Pattern to match key="value" pairs
    pattern = r'(\w+)="([^"]*)"'
    matches = re.findall(pattern, header_value)

    for key, value in matches:
        result[key] = value

    return result


async def handle_mcp_oauth_flow(
    auth_url: str,
    callback_port: int = 8765,
    timeout: float = 300.0,
) -> str:
    """Handle OAuth flow for MCP server authentication.

    This function:
    1. Starts a local HTTP server to receive the OAuth callback
    2. Opens the browser for user authorization
    3. Waits for the callback with the authorization code
    4. Exchanges the code for an access token (if token endpoint provided)

    Args:
        auth_url: OAuth authorization URL from WWW-Authenticate header
        callback_port: Port for local OAuth callback server
        timeout: Maximum time to wait for OAuth callback

    Returns:
        Access token or authorization code

    Raises:
        TimeoutError: If OAuth flow does not complete within timeout
        ValueError: If OAuth flow fails or is cancelled
    """
    # Reset class variables
    OAuthCallbackHandler.auth_code = None
    OAuthCallbackHandler.auth_state = None

    # Add callback URL to auth URL if not present
    parsed_auth_url = urlparse(auth_url)
    callback_url = f"http://localhost:{callback_port}/callback"

    if "redirect_uri" not in auth_url:
        separator = "&" if parsed_auth_url.query else "?"
        auth_url = f"{auth_url}{separator}redirect_uri={callback_url}"

    # Start local HTTP server in a separate thread
    server = HTTPServer(("localhost", callback_port), OAuthCallbackHandler)

    def run_server() -> None:
        server.handle_request()  # Handle only one request

    server_thread = Thread(target=run_server, daemon=True)
    server_thread.start()

    print("\n" + "=" * 70)
    print("🔐 MCP OAuth Authentication Required")
    print("=" * 70)
    print(f"\nOpening authorization URL in your browser:\n{auth_url}\n")
    print("Please complete the authentication in your browser.")
    print("The application will continue automatically once authenticated.")
    print("=" * 70 + "\n")

    # Open browser for authorization
    webbrowser.open(auth_url)

    # Wait for callback with timeout
    start_time = asyncio.get_event_loop().time()
    while OAuthCallbackHandler.auth_code is None:
        if asyncio.get_event_loop().time() - start_time > timeout:
            server.server_close()
            raise TimeoutError(f"OAuth authentication timed out after {timeout} seconds")
        await asyncio.sleep(0.5)

    server.server_close()

    if OAuthCallbackHandler.auth_code is None:
        raise ValueError("OAuth flow failed: No authorization code received")

    print("✅ Authentication successful!\n")

    # For now, return the authorization code
    # In a full implementation, you would exchange this for an access token
    return OAuthCallbackHandler.auth_code


async def probe_mcp_with_oauth(
    url: str,
    bearer_token: str | None = None,
    callback_port: int = 8765,
    timeout: float = 10.0,
) -> tuple[bool, str | None]:
    """Probe an MCP endpoint and handle OAuth if required.

    Args:
        url: MCP endpoint URL
        bearer_token: Optional existing bearer token
        callback_port: Port for OAuth callback server
        timeout: Connection timeout

    Returns:
        Tuple of (requires_oauth, auth_url_or_none)
        - (False, None) if connection succeeds
        - (True, auth_url) if OAuth is required

    Raises:
        httpx.HTTPStatusError: For non-401 HTTP errors
        Exception: For other connection errors
    """
    headers = {}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post(url, headers=headers or None)
            response.raise_for_status()
            return False, None  # Success, no OAuth needed

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                # Check for MCP OAuth in WWW-Authenticate header
                www_auth = e.response.headers.get("WWW-Authenticate", "")

                if "Model Context Protocol" in www_auth:
                    parsed = parse_www_authenticate_header(www_auth)
                    auth_url = parsed.get("auth_url")

                    if auth_url:
                        print(f"\n🔑 MCP OAuth required. Authorization URL: {auth_url}")
                        return True, auth_url

                # Re-raise if not MCP OAuth
                raise

            # Re-raise for other HTTP errors
            raise


async def get_mcp_token_with_oauth_if_needed(
    url: str,
    bearer_token: str | None = None,
    callback_port: int = 8765,
    timeout: float = 10.0,
    oauth_timeout: float = 300.0,
) -> str | None:
    """Get or refresh MCP token, handling OAuth flow if needed.

    Args:
        url: MCP endpoint URL
        bearer_token: Optional existing bearer token
        callback_port: Port for OAuth callback server
        timeout: Connection timeout
        oauth_timeout: OAuth flow timeout

    Returns:
        Bearer token (either existing or newly obtained via OAuth)

    Example:
        >>> token = await get_mcp_token_with_oauth_if_needed(
        ...     "https://mcp.figma.com/mcp",
        ...     bearer_token=os.getenv("FIGMA_TOKEN"),
        ... )
    """
    requires_oauth, auth_url = await probe_mcp_with_oauth(url, bearer_token, callback_port, timeout)

    if not requires_oauth:
        return bearer_token

    if auth_url is None:
        raise ValueError("OAuth required but no auth URL provided by server")

    # Run OAuth flow
    token = await handle_mcp_oauth_flow(auth_url, callback_port, oauth_timeout)

    return token
