"""
Thin async transport over the Instagram/Facebook Graph API.

Handles base-URL selection, GET / POST(form) / POST(json) / DELETE, access-token injection, and
normalization of Graph errors (``{error:{message,type,code,error_subcode,fbtrace_id}}``) into a
typed ``GraphError`` so tools can surface a clear message (e.g. code 190 → token expired).
"""

from __future__ import annotations

import json
from typing import Any, Optional

import httpx

from .config import IgConfig, LoginKind, graph_base_url


class GraphError(Exception):
    """Typed Graph API error with actionable hint messages."""

    def __init__(
        self,
        message: str,
        http_status: int,
        *,
        code: Optional[int] = None,
        subcode: Optional[int] = None,
        type: Optional[str] = None,
        fbtrace_id: Optional[str] = None,
    ):
        super().__init__(message)
        self.http_status = http_status
        self.code = code
        self.subcode = subcode
        self.type = type
        self.fbtrace_id = fbtrace_id

    def to_user_message(self) -> str:
        """Human-friendly, actionable one-liner for tool error output."""
        hint = ""
        if self.code == 190:
            hint = " — the access token is invalid/expired; refresh it."
        elif self.code in (10, 200):
            hint = " — the token is missing a required permission/Advanced Access for this action."
        elif self.code in (4, 17, 32, 613):
            hint = " — Instagram rate/usage limit hit; retry later."

        parts = [f"Instagram Graph API error: {self}"]
        if self.code is not None:
            sub = f"/{self.subcode}" if self.subcode else ""
            parts.append(f"(code {self.code}{sub})")
        return " ".join(parts) + hint


class InstagramClient:
    """Async HTTP client for the Instagram/Facebook Graph API."""

    def __init__(self, cfg: IgConfig):
        self._cfg = cfg
        self._http = httpx.AsyncClient(timeout=60.0)

    def _base(self, kind: Optional[LoginKind] = None) -> str:
        return graph_base_url(self._cfg, kind)

    @staticmethod
    def _parse_body(text: str) -> Any:
        """Parse response text as JSON, returning a raw wrapper on failure."""
        try:
            return json.loads(text) if text else {}
        except (json.JSONDecodeError, ValueError):
            return {"_raw": text}

    async def _handle_response(self, resp: httpx.Response) -> Any:
        """Parse the response and raise ``GraphError`` on API or HTTP errors."""
        text = resp.text
        body = self._parse_body(text)

        # Graph API error envelope
        if isinstance(body, dict) and "error" in body:
            e = body["error"]
            raise GraphError(
                e.get("message", "unknown error"),
                resp.status_code,
                code=e.get("code"),
                subcode=e.get("error_subcode"),
                type=e.get("type"),
                fbtrace_id=e.get("fbtrace_id"),
            )

        # Generic HTTP error
        if not resp.is_success:
            raw = body.get("_raw", "") if isinstance(body, dict) else ""
            msg = raw[:300] if raw else f"HTTP {resp.status_code}"
            raise GraphError(msg, resp.status_code)

        return body

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        form: Optional[dict[str, Any]] = None,
        json_body: Optional[Any] = None,
        token: Optional[str] = None,
        kind: Optional[LoginKind] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """Low-level send with token injection and error handling."""
        effective_token = (token or "").strip() or self._cfg.access_token
        url = f"{self._base(kind)}/{path.lstrip('/')}"

        # Build query params
        qp: dict[str, Any] = dict(params or {})
        if method != "POST" or json_body is not None:
            qp["access_token"] = effective_token
        # Filter out None values
        qp = {k: str(v) for k, v in qp.items() if v is not None}

        req_kwargs: dict[str, Any] = {"method": method, "url": url, "params": qp}
        if timeout:
            req_kwargs["timeout"] = timeout

        if method == "POST":
            if json_body is not None:
                req_kwargs["headers"] = {"content-type": "application/json"}
                req_kwargs["content"] = json.dumps(json_body)
            else:
                form_data: dict[str, str] = {}
                for k, v in (form or {}).items():
                    if v is not None:
                        form_data[k] = str(v)
                form_data["access_token"] = effective_token
                req_kwargs["data"] = form_data

        try:
            resp = await self._http.request(**req_kwargs)
            return await self._handle_response(resp)
        except GraphError:
            raise
        except Exception as exc:
            raise GraphError(f"network error: {exc}", 0) from exc

    async def get(
        self,
        path: str,
        params: Optional[dict[str, Any]] = None,
        opts: Optional[dict[str, Any]] = None,
    ) -> Any:
        """GET request."""
        opts = opts or {}
        return await self._send(
            "GET",
            path,
            params=params,
            token=opts.get("token"),
            kind=opts.get("kind"),
            timeout=opts.get("timeout", 30.0),
        )

    async def post(
        self,
        path: str,
        form: Optional[dict[str, Any]] = None,
        opts: Optional[dict[str, Any]] = None,
    ) -> Any:
        """POST request with form-encoded body."""
        opts = opts or {}
        return await self._send(
            "POST",
            path,
            form=form,
            token=opts.get("token"),
            kind=opts.get("kind"),
            timeout=opts.get("timeout"),
        )

    async def post_json(
        self,
        path: str,
        json_body: Any,
        opts: Optional[dict[str, Any]] = None,
    ) -> Any:
        """POST request with JSON body."""
        opts = opts or {}
        return await self._send(
            "POST",
            path,
            json_body=json_body,
            token=opts.get("token"),
            kind=opts.get("kind"),
            timeout=opts.get("timeout"),
        )

    async def delete(
        self,
        path: str,
        params: Optional[dict[str, Any]] = None,
        opts: Optional[dict[str, Any]] = None,
    ) -> Any:
        """DELETE request."""
        opts = opts or {}
        return await self._send(
            "DELETE",
            path,
            params=params,
            token=opts.get("token"),
            kind=opts.get("kind"),
            timeout=opts.get("timeout", 30.0),
        )
