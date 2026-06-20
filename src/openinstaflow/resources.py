"""
MCP Resources (application-controlled, read-only context the client can attach):
  - instagram://profile   → profile info incl. follower counts + bio
  - instagram://media     → recent posts with engagement metrics
  - instagram://insights  → account-level analytics

Reads are live (they hit the Graph API). Errors are returned as text rather than thrown, so a
failed read never hard-breaks the client.
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from .client import GraphError, InstagramClient
from .config import IgConfig


def register_resources(mcp: FastMCP, cfg: IgConfig) -> None:
    """Register all 3 Instagram resources on the MCP server."""
    client = InstagramClient(cfg)

    @mcp.resource(
        uri="instagram://profile",
        name="Instagram profile",
        description="Profile info for the configured account (username, bio, website, follower/following/media counts).",
        mime_type="application/json",
    )
    async def profile_resource() -> str:
        try:
            data = await client.get(
                cfg.ig_user_id,
                {
                    "fields": "username,name,biography,website,followers_count,follows_count,media_count,profile_picture_url"
                },
            )
            return json.dumps(data, indent=2)
        except GraphError as e:
            return f"Could not load this resource: {e.to_user_message()}"
        except Exception as e:
            return f"Could not load this resource: {e}"

    @mcp.resource(
        uri="instagram://media",
        name="Instagram media feed",
        description="The account's recent posts with engagement metrics (likes, comments).",
        mime_type="application/json",
    )
    async def media_resource() -> str:
        try:
            data = await client.get(
                f"{cfg.ig_user_id}/media",
                {
                    "fields": "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,like_count,comments_count",
                    "limit": 25,
                },
            )
            return json.dumps(data, indent=2)
        except GraphError as e:
            return f"Could not load this resource: {e.to_user_message()}"
        except Exception as e:
            return f"Could not load this resource: {e}"

    @mcp.resource(
        uri="instagram://insights",
        name="Instagram account insights",
        description="Account-level analytics (reach, accounts engaged, total interactions) for the last day.",
        mime_type="application/json",
    )
    async def insights_resource() -> str:
        try:
            data = await client.get(
                f"{cfg.ig_user_id}/insights",
                {
                    "metric": "reach,accounts_engaged,total_interactions",
                    "period": "day",
                    "metric_type": "total_value",
                },
            )
            return json.dumps(data, indent=2)
        except GraphError as e:
            return f"Could not load this resource: {e.to_user_message()}"
        except Exception as e:
            return f"Could not load this resource: {e}"
