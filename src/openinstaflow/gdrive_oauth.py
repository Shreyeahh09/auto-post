"""
Google OAuth2 (authorization code flow) for linking a customer's own Google Drive.

This is separate from the service-account flow in ``local_media.py`` (which uploads files
*to* a shared Drive owned by the operator for publishing). Here each customer grants
read-only access to their own Drive via the standard OAuth consent screen; the resulting
refresh token is stored encrypted on their ``Customer`` row and used by ``gdrive_sync.py``
to pull new media out of one folder they choose.

Requires a Google Cloud OAuth 2.0 "Web application" client:
  1. https://console.cloud.google.com -> APIs & Services -> Credentials -> Create OAuth client ID
  2. Add an authorized redirect URI matching GOOGLE_OAUTH_REDIRECT_URI
  3. Set GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET / GOOGLE_OAUTH_REDIRECT_URI in .env
"""

from __future__ import annotations

import os
from typing import Optional
from urllib.parse import urlencode

import httpx

from .database import decrypt_token, encrypt_token

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
]


def _client_id() -> str:
    return os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()


def _client_secret() -> str:
    return os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()


def _redirect_uri() -> str:
    return os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "").strip()


def encode_state(customer_id: str) -> str:
    """Pack the customer id into an opaque, tamper-evident state value (Fernet)."""
    return encrypt_token(customer_id)


def decode_state(state: str) -> Optional[str]:
    """Recover the customer id from a state value produced by ``encode_state``."""
    try:
        customer_id = decrypt_token(state)
        return customer_id or None
    except Exception:
        return None


def build_authorize_url(state: str) -> str:
    """Build the Google consent screen URL for a given opaque state value."""
    if not _client_id() or not _redirect_uri():
        raise RuntimeError(
            "GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_REDIRECT_URI not configured. "
            "Set them in .env to enable Google Drive linking."
        )
    params = {
        "client_id": _client_id(),
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> dict:
    """Exchange an authorization code for access/refresh tokens."""
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": _client_id(),
                "client_secret": _client_secret(),
                "redirect_uri": _redirect_uri(),
                "grant_type": "authorization_code",
            },
        )
    if not resp.is_success:
        raise RuntimeError(f"Google token exchange failed: HTTP {resp.status_code} {resp.text[:300]}")
    return resp.json()


async def refresh_access_token(refresh_token: str) -> str:
    """Exchange a stored refresh token for a fresh access token."""
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(
            TOKEN_URL,
            data={
                "refresh_token": refresh_token,
                "client_id": _client_id(),
                "client_secret": _client_secret(),
                "grant_type": "refresh_token",
            },
        )
    if not resp.is_success:
        raise RuntimeError(f"Google token refresh failed: HTTP {resp.status_code} {resp.text[:300]}")
    return resp.json()["access_token"]


async def fetch_user_email(access_token: str) -> Optional[str]:
    """Best-effort lookup of the connected Google account's email."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
        if resp.is_success:
            return resp.json().get("email")
    except Exception:
        pass
    return None


async def revoke_token(token: str) -> None:
    """Best-effort revoke of an access/refresh token (e.g. on disconnect)."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            await http.post(REVOKE_URL, data={"token": token})
    except Exception:
        pass
