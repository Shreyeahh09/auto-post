"""
Runtime configuration for the OpenInstaFlow MCP server.

Credentials are read once at startup from the environment (the MCP client sets these in the
server's ``env`` block). Every tool also accepts optional ``access_token`` / ``ig_user_id``
arguments that override the env values for that single call — so one running server can drive
several accounts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal, Optional

LoginKind = Literal["ig_login", "fb_login"]


def _env(name: str) -> Optional[str]:
    """Return a trimmed env var, or ``None`` if empty/unset."""
    v = os.environ.get(name, "")
    return v.strip() if v.strip() else None


@dataclass(frozen=True)
class IgConfig:
    access_token: str = ""
    ig_user_id: str = ""
    login_kind: LoginKind = "ig_login"
    graph_version: str = "v23.0"
    page_id: Optional[str] = None
    page_access_token: Optional[str] = None
    meta_app_id: Optional[str] = None
    meta_app_secret: Optional[str] = None


def load_config() -> IgConfig:
    """Read configuration from environment variables."""
    login_kind: LoginKind = "fb_login" if _env("IG_LOGIN_KIND") == "fb_login" else "ig_login"
    return IgConfig(
        access_token=_env("IG_ACCESS_TOKEN") or "",
        ig_user_id=_env("IG_USER_ID") or "",
        login_kind=login_kind,
        graph_version=_env("IG_GRAPH_VERSION") or "v23.0",
        page_id=_env("FB_PAGE_ID"),
        page_access_token=_env("FB_PAGE_ACCESS_TOKEN"),
        meta_app_id=_env("META_APP_ID"),
        meta_app_secret=_env("META_APP_SECRET"),
    )


def graph_base_url(cfg: IgConfig, kind: Optional[LoginKind] = None) -> str:
    """
    Base URL per login kind:
      - fb_login  → https://graph.facebook.com/{version}
      - ig_login  → https://graph.instagram.com
    """
    k = kind or cfg.login_kind
    if k == "fb_login":
        return f"https://graph.facebook.com/{cfg.graph_version}"
    return "https://graph.instagram.com"


def resolve(
    cfg: IgConfig,
    overrides: Optional[dict] = None,
) -> tuple[str, str]:
    """
    Resolve the effective access token and IG user id for a call,
    honoring per-call overrides.

    Returns:
        (token, user_id)
    """
    overrides = overrides or {}
    token = (overrides.get("access_token") or "").strip() or cfg.access_token
    user_id = (overrides.get("ig_user_id") or "").strip() or cfg.ig_user_id
    return token, user_id
