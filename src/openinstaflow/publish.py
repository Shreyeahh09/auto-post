"""
Instagram content-publishing flows:
  create media container → poll ``?fields=status_code`` until FINISHED → POST /{uid}/media_publish.

Cadences mirror the TypeScript/Python values: image 5×2s, reel 6×60s, story 6×5s, carousel 8×3s.
Functions return the published media id (+ permalink); they raise on any failure so the calling
tool can surface one clear error.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

from .client import InstagramClient
from .config import LoginKind


@dataclass
class PublishOutcome:
    media_id: str
    container_id: str
    permalink: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"media_id": self.media_id, "container_id": self.container_id}
        if self.permalink:
            d["permalink"] = self.permalink
        return d


@dataclass
class PublishCtx:
    client: InstagramClient
    user_id: str
    token: str
    kind: LoginKind


async def _poll_until_finished(
    ctx: PublishCtx,
    container_id: str,
    attempts: int,
    interval_s: float,
) -> None:
    """Poll the container status until FINISHED, ERROR, or timeout."""
    opts = {"token": ctx.token, "kind": ctx.kind}
    for _ in range(attempts):
        body = await ctx.client.get(container_id, {"fields": "status_code"}, opts)
        code = body.get("status_code") if isinstance(body, dict) else None
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"media container {code} (Instagram could not process the media)")
        await asyncio.sleep(interval_s)
    raise RuntimeError(f"media container still processing after {attempts} checks — try again shortly")


async def _publish_container(ctx: PublishCtx, container_id: str) -> PublishOutcome:
    """Publish a finished container and fetch its permalink."""
    opts = {"token": ctx.token, "kind": ctx.kind}
    pub = await ctx.client.post(
        f"{ctx.user_id}/media_publish",
        {"creation_id": container_id},
        opts,
    )
    if not isinstance(pub, dict) or not pub.get("id"):
        raise RuntimeError("media_publish returned no media id")

    permalink: Optional[str] = None
    try:
        m = await ctx.client.get(pub["id"], {"fields": "permalink"}, opts)
        permalink = m.get("permalink") if isinstance(m, dict) else None
    except Exception:
        pass  # permalink is best-effort

    return PublishOutcome(media_id=pub["id"], container_id=container_id, permalink=permalink)


async def publish_image(ctx: PublishCtx, image_url: str, caption: Optional[str] = None) -> PublishOutcome:
    """Publish a single image post."""
    opts = {"token": ctx.token, "kind": ctx.kind}
    c = await ctx.client.post(
        f"{ctx.user_id}/media",
        {"image_url": image_url, "caption": caption},
        opts,
    )
    if not isinstance(c, dict) or not c.get("id"):
        raise RuntimeError("image container creation returned no id")
    await _poll_until_finished(ctx, c["id"], attempts=5, interval_s=2.0)
    return await _publish_container(ctx, c["id"])


async def publish_reel(
    ctx: PublishCtx,
    video_url: str,
    caption: Optional[str] = None,
    *,
    share_to_feed: Optional[bool] = None,
    cover_url: Optional[str] = None,
) -> PublishOutcome:
    """Publish a reel/video post."""
    opts = {"token": ctx.token, "kind": ctx.kind}
    form: dict[str, Any] = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": "true" if (share_to_feed is None or share_to_feed) else "false",
    }
    if cover_url:
        form["cover_url"] = cover_url
    c = await ctx.client.post(f"{ctx.user_id}/media", form, opts)
    if not isinstance(c, dict) or not c.get("id"):
        raise RuntimeError("reel container creation returned no id")
    await _poll_until_finished(ctx, c["id"], attempts=6, interval_s=60.0)  # Reels encode slowly: ~6 min
    return await _publish_container(ctx, c["id"])


async def publish_story(
    ctx: PublishCtx,
    *,
    image_url: Optional[str] = None,
    video_url: Optional[str] = None,
) -> PublishOutcome:
    """Publish a story (image or video)."""
    opts = {"token": ctx.token, "kind": ctx.kind}
    form: dict[str, Any] = {"media_type": "STORIES"}
    if video_url:
        form["video_url"] = video_url
    elif image_url:
        form["image_url"] = image_url
    else:
        raise RuntimeError("a story needs image_url or video_url")
    c = await ctx.client.post(f"{ctx.user_id}/media", form, opts)
    if not isinstance(c, dict) or not c.get("id"):
        raise RuntimeError("story container creation returned no id")
    await _poll_until_finished(ctx, c["id"], attempts=6, interval_s=5.0)
    return await _publish_container(ctx, c["id"])


async def publish_carousel(
    ctx: PublishCtx,
    items: list[dict[str, str]],
    caption: Optional[str] = None,
) -> PublishOutcome:
    """
    Publish a carousel post (2–10 items).

    Each item: ``{"type": "image"|"video", "url": "<public URL>"}``.
    """
    if len(items) < 2 or len(items) > 10:
        raise RuntimeError("a carousel needs between 2 and 10 items")

    opts = {"token": ctx.token, "kind": ctx.kind}
    child_ids: list[str] = []

    for item in items:
        form: dict[str, Any] = {"is_carousel_item": "true"}
        if item["type"] == "video":
            form["media_type"] = "VIDEO"
            form["video_url"] = item["url"]
        else:
            form["image_url"] = item["url"]
        child = await ctx.client.post(f"{ctx.user_id}/media", form, opts)
        if not isinstance(child, dict) or not child.get("id"):
            raise RuntimeError("carousel child container returned no id")
        child_ids.append(child["id"])

    parent = await ctx.client.post(
        f"{ctx.user_id}/media",
        {"media_type": "CAROUSEL", "children": ",".join(child_ids), "caption": caption},
        opts,
    )
    if not isinstance(parent, dict) or not parent.get("id"):
        raise RuntimeError("carousel container creation returned no id")
    await _poll_until_finished(ctx, parent["id"], attempts=8, interval_s=3.0)
    return await _publish_container(ctx, parent["id"])
