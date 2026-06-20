"""
The 8 OpenInstaFlow Instagram tools (model-controlled).

Each tool resolves the effective token + account (honoring per-call overrides), calls the Graph
API, and returns the JSON result as pretty text. Graph errors are caught and surfaced as a clear,
actionable message via ``GraphError.to_user_message()``.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from .client import GraphError, InstagramClient
from .config import IgConfig, LoginKind, resolve
from .local_media import convert_gdrive_url, detect_media_type, get_google_drive_uploader, shutdown_google_drive_uploader
from .publish import PublishCtx, publish_carousel, publish_image, publish_reel, publish_story
from .scheduler import PostScheduler, ScheduledPost, get_post_scheduler, shutdown_post_scheduler


def _err_text(err: Exception) -> str:
    if isinstance(err, GraphError):
        return err.to_user_message()
    return str(err)


def _messaging(
    cfg: IgConfig,
    access_token: Optional[str] = None,
    ig_user_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Resolve the actor id + token + base for Instagram Messaging (Pages/DMs).
    Returns dict with 'kind', 'actor', 'token' on success, or 'error' string on failure.
    """
    if cfg.login_kind == "fb_login":
        actor = (ig_user_id or "").strip() or cfg.page_id
        token = (access_token or "").strip() or cfg.page_access_token or cfg.access_token
        if not actor:
            return {
                "error": "Instagram messaging via Facebook Login needs a Page id. "
                "Set FB_PAGE_ID (and ideally FB_PAGE_ACCESS_TOKEN), then retry."
            }
        return {"kind": "fb_login", "actor": actor, "token": token}

    token, user_id = resolve(cfg, {"access_token": access_token, "ig_user_id": ig_user_id})
    if not user_id:
        return {"error": "Messaging needs an IG_USER_ID (or pass ig_user_id)."}
    return {"kind": "ig_login", "actor": user_id, "token": token}


ADV_ACCESS_NOTE = (
    "Requires Advanced Access (`instagram_manage_messages`) on a Business/Creator account. "
    "If the token lacks it, Instagram returns a permission error."
)


def register_tools(mcp: FastMCP, cfg: IgConfig) -> None:
    """Register all 8 Instagram tools on the MCP server."""
    client = InstagramClient(cfg)

    # ── 1. get_profile_info ───────────────────────────────────────────────────
    @mcp.tool(
        name="get_profile_info",
        description=(
            "Retrieve the Instagram business/creator profile: username, name, biography, "
            "website, follower/following/media counts, and profile picture."
        ),
    )
    async def get_profile_info(
        fields: str = "username,name,biography,website,followers_count,follows_count,media_count,profile_picture_url",
        access_token: Optional[str] = None,
        ig_user_id: Optional[str] = None,
    ) -> str:
        """Get Instagram profile info."""
        try:
            token, user_id = resolve(cfg, {"access_token": access_token, "ig_user_id": ig_user_id})
            if not user_id:
                return "Error: No IG_USER_ID configured (and none passed). Set it in env or pass ig_user_id."
            data = await client.get(user_id, {"fields": fields}, {"token": token})
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {_err_text(e)}"

    # ── 2. get_media_posts ────────────────────────────────────────────────────
    @mcp.tool(
        name="get_media_posts",
        description=(
            "Fetch recent posts from the Instagram account (id, caption, type, media_url, permalink, "
            "timestamp, like/comment counts). Supports paging via `after`."
        ),
    )
    async def get_media_posts(
        limit: int = 25,
        after: Optional[str] = None,
        fields: str = "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,like_count,comments_count",
        access_token: Optional[str] = None,
        ig_user_id: Optional[str] = None,
    ) -> str:
        """Get recent media posts."""
        try:
            token, user_id = resolve(cfg, {"access_token": access_token, "ig_user_id": ig_user_id})
            if not user_id:
                return "Error: No IG_USER_ID configured (and none passed)."
            params: dict[str, Any] = {"fields": fields, "limit": limit}
            if after:
                params["after"] = after
            data = await client.get(f"{user_id}/media", params, {"token": token})
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {_err_text(e)}"

    # ── 3. get_media_insights ─────────────────────────────────────────────────
    @mcp.tool(
        name="get_media_insights",
        description=(
            "Retrieve engagement metrics for one media object. Available metrics depend on the "
            "media type (FEED/REELS/STORY) — pass `metrics` to customize."
        ),
    )
    async def get_media_insights(
        media_id: str,
        metrics: str = "reach,likes,comments,saved,shares",
        access_token: Optional[str] = None,
        ig_user_id: Optional[str] = None,
    ) -> str:
        """Get media insights."""
        try:
            token, _ = resolve(cfg, {"access_token": access_token, "ig_user_id": ig_user_id})
            data = await client.get(f"{media_id}/insights", {"metric": metrics}, {"token": token})
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {_err_text(e)}"

    # ── 4. publish_media ──────────────────────────────────────────────────────
    @mcp.tool(
        name="publish_media",
        description=(
            "Publish an image, reel/video, story, or carousel. Media must be a PUBLIC HTTPS URL "
            "(Instagram fetches it). Google Drive share links are auto-converted to direct URLs. "
            "Reels can take a few minutes to process. Type is inferred from the inputs if "
            "`media_type` is omitted."
        ),
    )
    async def publish_media(
        image_url: Optional[str] = None,
        video_url: Optional[str] = None,
        caption: Optional[str] = None,
        media_type: Optional[str] = None,
        share_to_feed: Optional[bool] = None,
        cover_url: Optional[str] = None,
        carousel_items: Optional[list[dict[str, str]]] = None,
        access_token: Optional[str] = None,
        ig_user_id: Optional[str] = None,
    ) -> str:
        """Publish media to Instagram."""
        try:
            token, user_id = resolve(cfg, {"access_token": access_token, "ig_user_id": ig_user_id})
            if not user_id:
                return "Error: No IG_USER_ID configured (and none passed)."
            ctx = PublishCtx(client=client, user_id=user_id, token=token, kind=cfg.login_kind)

            # Auto-convert Google Drive share links to direct download URLs
            if image_url:
                image_url = convert_gdrive_url(image_url)
            if video_url:
                video_url = convert_gdrive_url(video_url)
            if carousel_items:
                carousel_items = [
                    {**item, "url": convert_gdrive_url(item.get("url", ""))}
                    for item in carousel_items
                ]

            if media_type == "carousel" or carousel_items:
                if not carousel_items:
                    return "Error: carousel_items is required for a carousel post."
                outcome = await publish_carousel(ctx, carousel_items, caption)
                return json.dumps({"status": "published", **outcome.to_dict()}, indent=2)

            if media_type == "story":
                outcome = await publish_story(ctx, image_url=image_url, video_url=video_url)
                return json.dumps({"status": "published", **outcome.to_dict()}, indent=2)

            if media_type == "reel" or (video_url and media_type != "image"):
                if not video_url:
                    return "Error: video_url is required for a reel."
                outcome = await publish_reel(
                    ctx, video_url, caption, share_to_feed=share_to_feed, cover_url=cover_url
                )
                return json.dumps({"status": "published", **outcome.to_dict()}, indent=2)

            if image_url:
                outcome = await publish_image(ctx, image_url, caption)
                return json.dumps({"status": "published", **outcome.to_dict()}, indent=2)

            return "Error: Provide image_url (image), video_url (reel), or carousel_items (carousel)."
        except Exception as e:
            return f"Error: {_err_text(e)}"

    # ── 5. get_account_pages ──────────────────────────────────────────────────
    @mcp.tool(
        name="get_account_pages",
        description=(
            "List the Facebook Pages connected to the account, each with its page access token "
            "and linked Instagram business account. Requires Facebook Login (IG_LOGIN_KIND=fb_login)."
        ),
    )
    async def get_account_pages(
        access_token: Optional[str] = None,
        ig_user_id: Optional[str] = None,
    ) -> str:
        """Get connected Facebook Pages."""
        try:
            if cfg.login_kind != "fb_login":
                return (
                    "Error: get_account_pages requires Facebook Login. "
                    "Set IG_LOGIN_KIND=fb_login with a Facebook user access token."
                )
            token = (access_token or "").strip() or cfg.access_token
            data = await client.get(
                "me/accounts",
                {"fields": "id,name,access_token,instagram_business_account{id,username}"},
                {"token": token, "kind": "fb_login"},
            )
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {_err_text(e)}"

    # ── 6. get_conversations ──────────────────────────────────────────────────
    @mcp.tool(
        name="get_conversations",
        description=f"List Instagram Direct Message conversation threads. {ADV_ACCESS_NOTE}",
    )
    async def get_conversations(
        limit: int = 20,
        access_token: Optional[str] = None,
        ig_user_id: Optional[str] = None,
    ) -> str:
        """Get Instagram DM conversations."""
        try:
            m = _messaging(cfg, access_token, ig_user_id)
            if "error" in m:
                return f"Error: {m['error']}"
            params: dict[str, Any] = {
                "platform": "instagram",
                "fields": "id,updated_time,participants",
                "limit": limit,
            }
            data = await client.get(
                f"{m['actor']}/conversations", params, {"token": m["token"], "kind": m["kind"]}
            )
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {_err_text(e)}"

    # ── 7. get_conversation_messages ──────────────────────────────────────────
    @mcp.tool(
        name="get_conversation_messages",
        description=f"Read messages from a specific Instagram DM conversation. {ADV_ACCESS_NOTE}",
    )
    async def get_conversation_messages(
        conversation_id: str,
        limit: int = 25,
        access_token: Optional[str] = None,
        ig_user_id: Optional[str] = None,
    ) -> str:
        """Get messages in a conversation."""
        try:
            m = _messaging(cfg, access_token, ig_user_id)
            if "error" in m:
                return f"Error: {m['error']}"
            data = await client.get(
                f"{conversation_id}/messages",
                {"fields": "id,created_time,from,to,message", "limit": limit},
                {"token": m["token"], "kind": m["kind"]},
            )
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {_err_text(e)}"

    # ── 8. send_dm ────────────────────────────────────────────────────────────
    @mcp.tool(
        name="send_dm",
        description=(
            f"Send/reply to an Instagram Direct Message. {ADV_ACCESS_NOTE} "
            "Standard messaging-window rules apply (you can generally only message users "
            "who messaged you within 24h, unless using a valid message tag)."
        ),
    )
    async def send_dm(
        recipient_id: str,
        message: str,
        access_token: Optional[str] = None,
        ig_user_id: Optional[str] = None,
    ) -> str:
        """Send an Instagram DM."""
        try:
            m = _messaging(cfg, access_token, ig_user_id)
            if "error" in m:
                return f"Error: {m['error']}"
            body = {"recipient": {"id": recipient_id}, "message": {"text": message}}
            data = await client.post_json(
                f"{m['actor']}/messages", body, {"token": m["token"], "kind": m["kind"]}
            )
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {_err_text(e)}"

    # ── 9. publish_local_media ────────────────────────────────────────────────
    @mcp.tool(
        name="publish_local_media",
        description=(
            "Publish a local image or video file to Instagram. The file is uploaded to Google Drive "
            "(made public) so Instagram can fetch it. Requires GOOGLE_SERVICE_ACCOUNT_JSON in .env. "
            "Supported formats: jpg, jpeg, png, gif, bmp, webp (images), mp4, mov, avi, mkv (videos)."
        ),
    )
    async def publish_local_media(
        local_path: str,
        caption: Optional[str] = None,
        media_type: Optional[str] = None,
        share_to_feed: Optional[bool] = None,
        cover_url: Optional[str] = None,
        access_token: Optional[str] = None,
        ig_user_id: Optional[str] = None,
    ) -> str:
        """Publish a local media file to Instagram."""
        try:
            import asyncio

            token, user_id = resolve(cfg, {"access_token": access_token, "ig_user_id": ig_user_id})
            if not user_id:
                return "Error: No IG_USER_ID configured (and none passed)."

            # Detect media type from file extension
            detected = detect_media_type(local_path)
            effective_type = media_type or ("reel" if detected == "video" else "image")

            # Upload to Google Drive and get public URL
            uploader = get_google_drive_uploader()
            loop = asyncio.get_event_loop()
            public_url, file_id = await loop.run_in_executor(None, uploader.upload_file, local_path)

            ctx = PublishCtx(client=client, user_id=user_id, token=token, kind=cfg.login_kind)

            if effective_type == "story":
                if detected == "video":
                    outcome = await publish_story(ctx, video_url=public_url)
                else:
                    outcome = await publish_story(ctx, image_url=public_url)
            elif effective_type == "reel":
                outcome = await publish_reel(
                    ctx, public_url, caption, share_to_feed=share_to_feed, cover_url=cover_url
                )
            else:  # image
                outcome = await publish_image(ctx, public_url, caption)

            # Clean up the Google Drive file after publishing
            await loop.run_in_executor(None, uploader.delete_file, file_id)

            return json.dumps({"status": "published", "local_path": local_path, **outcome.to_dict()}, indent=2)
        except Exception as e:
            return f"Error: {_err_text(e)}"

    # ── 10. schedule_post ─────────────────────────────────────────────────────
    @mcp.tool(
        name="schedule_post",
        description=(
            "Schedule an Instagram post for future publishing. Provide a datetime in ISO 8601 "
            "format (e.g. '2026-06-08T15:00:00+05:30'). Works with both public URLs and local "
            "file paths (requires GOOGLE_SERVICE_ACCOUNT_JSON for local files)."
        ),
    )
    async def schedule_post(
        scheduled_time: str,
        image_url: Optional[str] = None,
        video_url: Optional[str] = None,
        local_path: Optional[str] = None,
        caption: Optional[str] = None,
        media_type: Optional[str] = None,
        share_to_feed: Optional[bool] = None,
        cover_url: Optional[str] = None,
        access_token: Optional[str] = None,
        ig_user_id: Optional[str] = None,
    ) -> str:
        """Schedule a post for future publishing."""
        try:
            from datetime import datetime as dt, timezone

            token, user_id = resolve(cfg, {"access_token": access_token, "ig_user_id": ig_user_id})
            if not user_id:
                return "Error: No IG_USER_ID configured (and none passed)."

            # Parse scheduled time
            try:
                publish_time = dt.fromisoformat(scheduled_time)
            except ValueError:
                return f"Error: Invalid datetime format: '{scheduled_time}'. Use ISO 8601, e.g. '2026-06-08T15:00:00+05:30'."

            # Determine media source and type
            is_local = False
            if local_path:
                media_source = local_path
                is_local = True
                detected = detect_media_type(local_path)
                effective_type = media_type or ("reel" if detected == "video" else "image")
            elif video_url:
                media_source = video_url
                effective_type = media_type or "reel"
            elif image_url:
                media_source = image_url
                effective_type = media_type or "image"
            else:
                return "Error: Provide image_url, video_url, or local_path."

            # Build the publish callback
            async def _do_publish(post: ScheduledPost) -> dict:
                import asyncio as _asyncio

                _token, _uid = resolve(cfg, {"access_token": access_token, "ig_user_id": ig_user_id})
                _ctx = PublishCtx(client=client, user_id=_uid, token=_token, kind=cfg.login_kind)
                src = post.media_source

                # If local, upload to Google Drive first
                if post.is_local:
                    _uploader = get_google_drive_uploader()
                    _loop = _asyncio.get_event_loop()
                    src, _file_id = await _loop.run_in_executor(None, _uploader.upload_file, post.media_source)

                if post.media_type == "story":
                    detected_type = detect_media_type(post.media_source) if post.is_local else ("video" if post.media_source.endswith((".mp4", ".mov")) else "image")
                    if detected_type == "video":
                        outcome = await publish_story(_ctx, video_url=src)
                    else:
                        outcome = await publish_story(_ctx, image_url=src)
                elif post.media_type == "reel":
                    outcome = await publish_reel(
                        _ctx, src, post.caption, share_to_feed=post.share_to_feed, cover_url=post.cover_url
                    )
                else:  # image
                    outcome = await publish_image(_ctx, src, post.caption)

                return {"status": "published", **outcome.to_dict()}

            scheduler = get_post_scheduler()
            job_id = scheduler.schedule_post(
                scheduled_time=publish_time,
                media_type=effective_type,
                media_source=media_source,
                publish_callback=_do_publish,
                caption=caption,
                share_to_feed=share_to_feed,
                cover_url=cover_url,
                is_local=is_local,
            )

            return json.dumps({
                "status": "scheduled",
                "job_id": job_id,
                "scheduled_time": publish_time.isoformat(),
                "media_type": effective_type,
                "media_source": media_source,
                "is_local": is_local,
                "caption": caption,
            }, indent=2)
        except Exception as e:
            return f"Error: {_err_text(e)}"

    # ── 11. list_scheduled_posts ──────────────────────────────────────────────
    @mcp.tool(
        name="list_scheduled_posts",
        description="List all scheduled Instagram posts with their status (pending, published, failed, cancelled).",
    )
    async def list_scheduled_posts(
        include_completed: bool = True,
    ) -> str:
        """List all scheduled posts."""
        try:
            scheduler = get_post_scheduler()
            posts = scheduler.list_scheduled(include_completed=include_completed)
            if not posts:
                return json.dumps({"message": "No scheduled posts found.", "posts": []}, indent=2)
            return json.dumps({"count": len(posts), "posts": posts}, indent=2)
        except Exception as e:
            return f"Error: {_err_text(e)}"

    # ── 12. cancel_scheduled_post ─────────────────────────────────────────────
    @mcp.tool(
        name="cancel_scheduled_post",
        description="Cancel a pending scheduled Instagram post by its job ID.",
    )
    async def cancel_scheduled_post(
        job_id: str,
    ) -> str:
        """Cancel a scheduled post."""
        try:
            scheduler = get_post_scheduler()
            success = scheduler.cancel_scheduled(job_id)
            if success:
                return json.dumps({"status": "cancelled", "job_id": job_id}, indent=2)
            post = scheduler.get_post(job_id)
            if post:
                return json.dumps({
                    "error": f"Cannot cancel post '{job_id}' — status is '{post['status']}'.",
                    "post": post,
                }, indent=2)
            return json.dumps({"error": f"No scheduled post found with ID '{job_id}'."}, indent=2)
        except Exception as e:
            return f"Error: {_err_text(e)}"

