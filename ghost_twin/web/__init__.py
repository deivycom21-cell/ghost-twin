"""Live dark dashboard (FastAPI + WebSockets + vanilla JS) on 127.0.0.1:8585."""

from ghost_twin.web.server import DashboardThread, create_app, serve

__all__ = ["DashboardThread", "create_app", "serve"]
