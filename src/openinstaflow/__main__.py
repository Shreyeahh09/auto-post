"""
OpenInstaFlow — Instagram MCP server (stdio).

Exposes the Instagram Graph API as MCP Tools + Resources + Prompts. Bring your own Meta token
(env IG_ACCESS_TOKEN + IG_USER_ID, or per-call overrides). Launch via ``python -m openinstaflow``.

NOTE: stdout is the MCP protocol channel — all logging MUST go to stderr.
"""

from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP

from .config import load_config
from .prompts import register_prompts
from .resources import register_resources
from .tools import register_tools


def main() -> None:
    cfg = load_config()

    if not cfg.access_token:
        print(
            "[OpenInstaFlow] WARNING: IG_ACCESS_TOKEN is not set. "
            "Tools will fail until a token is provided (server env or per-call access_token).",
            file=sys.stderr,
        )

    mcp = FastMCP(
        name="OpenInstaFlow",
    )

    register_tools(mcp, cfg)
    register_resources(mcp, cfg)
    register_prompts(mcp)

    print(
        f"[OpenInstaFlow] MCP server ready on stdio "
        f"(login={cfg.login_kind}, account={cfg.ig_user_id or 'unset'}).",
        file=sys.stderr,
    )

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
