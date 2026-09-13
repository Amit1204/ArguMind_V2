"""Dependencies shared by routers."""

from __future__ import annotations

from fastapi import Request

from app.config import Settings


def get_app_settings(request: Request) -> Settings:
    """The Settings instance the app was created with (tests pass their own)."""
    return request.app.state.settings
