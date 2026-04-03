"""Boot script for the akgentic-infra community-tier server."""

from __future__ import annotations

import uvicorn
from akgentic.infra.server.app import create_app
from akgentic.infra.server.settings import CommunitySettings
from akgentic.infra.wiring import wire_community

settings = CommunitySettings()
services = wire_community(settings)
app = create_app(services, settings)

if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port)
