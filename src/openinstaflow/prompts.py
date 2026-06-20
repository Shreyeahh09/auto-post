"""
MCP Prompts (user-controlled templates). Each returns a user message that steers the model to
call the OpenInstaFlow tools / read the resources and produce a focused analysis.
"""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent


def register_prompts(mcp: FastMCP) -> None:
    """Register all 3 Instagram prompts on the MCP server."""

    @mcp.prompt(
        name="analyze_engagement",
        description="Analyze recent post performance and surface what's working.",
    )
    async def analyze_engagement(limit: Optional[str] = None) -> str:
        limit_val = (limit or "").strip() or "12"
        return (
            f"Analyze the engagement of my Instagram account.\n"
            f"1. Call get_media_posts (limit {limit_val}) to fetch recent posts.\n"
            f"2. For the top few by likes/comments, call get_media_insights to pull reach/saves/shares.\n"
            f"3. Summarize what's performing best (formats, themes, posting cadence), what's underperforming, "
            f"and give 3 concrete, data-backed recommendations to improve engagement."
        )

    @mcp.prompt(
        name="content_strategy",
        description="Generate a content strategy / recommendations for the account.",
    )
    async def content_strategy(niche: Optional[str] = None, goal: Optional[str] = None) -> str:
        niche_val = (niche or "").strip() or "(infer the niche from the profile + recent posts)"
        goal_val = (goal or "").strip() or "grow reach and engagement"
        return (
            f"Build an Instagram content strategy.\n"
            f"Niche: {niche_val}\nGoal: {goal_val}\n"
            f"1. Read the instagram://profile and instagram://media resources to ground in the real account.\n"
            f"2. Propose a weekly content plan: post types (reels/carousels/images), themes/series, captions style, "
            f"posting cadence, and hashtag approach.\n"
            f"3. Give 5 concrete post ideas (hook + format + caption angle) tailored to the goal."
        )

    @mcp.prompt(
        name="hashtag_analysis",
        description="Evaluate hashtag performance and recommend a better set.",
    )
    async def hashtag_analysis(hashtags: Optional[str] = None) -> str:
        tags = (hashtags or "").strip()
        if tags:
            tag_line = f"Evaluate these hashtags: {tags}.\n"
        else:
            tag_line = "First call get_media_posts and extract the hashtags currently used in recent captions.\n"
        return (
            f"Evaluate my Instagram hashtag strategy.\n"
            f"{tag_line}"
            f"1. Group them by reach potential (broad vs. niche vs. branded) and flag any that are banned/overused/irrelevant.\n"
            f"2. Recommend a balanced set (mix of sizes) tailored to the account's niche.\n"
            f"3. Explain the reasoning briefly so I can reuse the approach."
        )
