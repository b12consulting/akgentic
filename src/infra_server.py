"""Boot script for the akgentic-infra community-tier server."""

from __future__ import annotations

from pathlib import Path

import logfire
import uvicorn
from akgentic.infra.server.app import create_app
from akgentic.infra.server.settings import CommunitySettings
from akgentic.infra.wiring import wire_community

settings = CommunitySettings(catalog_path=Path("./src/catalog"))
services = wire_community(settings)
app = create_app(services, settings)

if __name__ == "__main__":
    logfire.configure(console=False)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        timeout_graceful_shutdown=1,
    )
