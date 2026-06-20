#!/usr/bin/env python3
"""
OpenAI ↔ OpenInstaFlow Bridge
==============================
Connects OpenAI's function-calling API to the Instagram Graph API via the
OpenInstaFlow client. Supports interactive chat and one-shot ``--prompt`` mode.

Usage:
    python openai_bridge.py                          # interactive chat
    python openai_bridge.py --prompt "Get my profile" # one-shot
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

try:
    from openai import OpenAI
except ImportError:
    print("ERROR: openai package not installed. Run: pip install openai", file=sys.stderr)
    sys.exit(1)

try:
    from dotenv import load_dotenv
except ImportError:
    print("ERROR: python-dotenv not installed. Run: pip install python-dotenv", file=sys.stderr)
    sys.exit(1)

from openinstaflow.client import GraphError, InstagramClient
from openinstaflow.config import IgConfig, load_config, resolve
from openinstaflow.local_media import (
    convert_gdrive_url,
    detect_media_type,
    get_google_drive_uploader,
    shutdown_google_drive_uploader,
)
from openinstaflow.publish import (
    PublishCtx,
    publish_carousel,
    publish_image,
    publish_reel,
    publish_story,
)
from openinstaflow.scheduler import (
    ScheduledPost,
    get_post_scheduler,
    shutdown_post_scheduler,
)

# ──────────────────────────────────────────────────────────────────────────────
# OpenAI function/tool definitions (mirrors the 8 MCP tools)
# ──────────────────────────────────────────────────────────────────────────────

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_profile_info",
            "description": (
                "Retrieve the Instagram business/creator profile: username, name, biography, "
                "website, follower/following/media counts, and profile picture."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "fields": {
                        "type": "string",
                        "description": "Comma-separated Graph fields (defaults to common profile fields).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_media_posts",
            "description": (
                "Fetch recent posts from the Instagram account (id, caption, type, media_url, "
                "permalink, timestamp, like/comment counts). Supports paging via `after`."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "How many posts to return (1-100, default 25).",
                    },
                    "after": {
                        "type": "string",
                        "description": "Paging cursor from a previous response.",
                    },
                    "fields": {
                        "type": "string",
                        "description": "Override the Graph fields to fetch.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_media_insights",
            "description": (
                "Retrieve engagement metrics for one media object. Available metrics depend on "
                "the media type (FEED/REELS/STORY)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "media_id": {
                        "type": "string",
                        "description": "The media id (from get_media_posts).",
                    },
                    "metrics": {
                        "type": "string",
                        "description": "Comma-separated metrics (default: reach,likes,comments,saved,shares).",
                    },
                },
                "required": ["media_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "publish_media",
            "description": (
                "Publish an image, reel/video, story, or carousel to Instagram. "
                "Media must be a PUBLIC HTTPS URL (Instagram fetches it server-side). "
                "Google Drive share links are automatically converted to direct URLs. "
                "Reels can take a few minutes to process."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "image_url": {
                        "type": "string",
                        "description": "Public HTTPS URL of an image.",
                    },
                    "video_url": {
                        "type": "string",
                        "description": "Public HTTPS URL of a video (for reel/story).",
                    },
                    "caption": {
                        "type": "string",
                        "description": "Caption text (include hashtags here).",
                    },
                    "media_type": {
                        "type": "string",
                        "enum": ["image", "reel", "story", "carousel"],
                        "description": "Force the post type; inferred if omitted.",
                    },
                    "share_to_feed": {
                        "type": "boolean",
                        "description": "Reels only: also show on the main feed (default true).",
                    },
                    "cover_url": {
                        "type": "string",
                        "description": "Reels only: public URL of a cover image.",
                    },
                    "carousel_items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "enum": ["image", "video"]},
                                "url": {"type": "string"},
                            },
                            "required": ["type", "url"],
                        },
                        "description": "For a carousel: 2-10 items, each with type and url.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_account_pages",
            "description": (
                "List the Facebook Pages connected to the account. "
                "Requires Facebook Login (IG_LOGIN_KIND=fb_login)."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_conversations",
            "description": (
                "List Instagram Direct Message conversation threads. "
                "Requires Advanced Access (instagram_manage_messages)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "How many conversations to return (default 20).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_conversation_messages",
            "description": "Read messages from a specific Instagram DM conversation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "conversation_id": {
                        "type": "string",
                        "description": "The conversation id (from get_conversations).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "How many messages to return (default 25).",
                    },
                },
                "required": ["conversation_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_dm",
            "description": (
                "Send/reply to an Instagram Direct Message. "
                "Standard messaging-window rules apply."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "recipient_id": {
                        "type": "string",
                        "description": "The Instagram-scoped id (IGSID) of the recipient.",
                    },
                    "message": {
                        "type": "string",
                        "description": "The text to send.",
                    },
                },
                "required": ["recipient_id", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "publish_local_media",
            "description": (
                "Publish a local image or video file from the user's computer to Instagram. "
                "The file is uploaded to Google Drive (made public) so Instagram can fetch it. "
                "Requires GOOGLE_SERVICE_ACCOUNT_JSON. "
                "Supported: jpg, jpeg, png, gif, bmp, webp (images), mp4, mov, avi, mkv (videos)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "local_path": {
                        "type": "string",
                        "description": "Absolute path to a local image or video file.",
                    },
                    "caption": {
                        "type": "string",
                        "description": "Caption text (include hashtags here).",
                    },
                    "media_type": {
                        "type": "string",
                        "enum": ["image", "reel", "story"],
                        "description": "Force the post type; inferred from file extension if omitted.",
                    },
                    "share_to_feed": {
                        "type": "boolean",
                        "description": "Reels only: also show on the main feed (default true).",
                    },
                    "cover_url": {
                        "type": "string",
                        "description": "Reels only: public URL of a cover image.",
                    },
                },
                "required": ["local_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_post",
            "description": (
                "Schedule an Instagram post for future publishing. Provide a datetime in ISO 8601 "
                "format (e.g. '2026-06-08T15:00:00+05:30'). Works with public URLs or local file paths."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scheduled_time": {
                        "type": "string",
                        "description": "ISO 8601 datetime for when to publish (e.g. '2026-06-08T15:00:00+05:30').",
                    },
                    "image_url": {
                        "type": "string",
                        "description": "Public HTTPS URL of an image.",
                    },
                    "video_url": {
                        "type": "string",
                        "description": "Public HTTPS URL of a video.",
                    },
                    "local_path": {
                        "type": "string",
                        "description": "Absolute path to a local image or video file.",
                    },
                    "caption": {
                        "type": "string",
                        "description": "Caption text.",
                    },
                    "media_type": {
                        "type": "string",
                        "enum": ["image", "reel", "story"],
                        "description": "Force the post type; inferred if omitted.",
                    },
                    "share_to_feed": {
                        "type": "boolean",
                        "description": "Reels only: also show on the main feed.",
                    },
                    "cover_url": {
                        "type": "string",
                        "description": "Reels only: public URL of a cover image.",
                    },
                },
                "required": ["scheduled_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_scheduled_posts",
            "description": "List all scheduled Instagram posts with their status (pending, published, failed, cancelled).",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_completed": {
                        "type": "boolean",
                        "description": "Include published/failed/cancelled posts (default true).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_scheduled_post",
            "description": "Cancel a pending scheduled Instagram post by its job ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "The job ID from schedule_post.",
                    },
                },
                "required": ["job_id"],
            },
        },
    },
]

# ──────────────────────────────────────────────────────────────────────────────
# Tool execution — calls the real Instagram Graph API
# ──────────────────────────────────────────────────────────────────────────────


def _err_text(err: Exception) -> str:
    if isinstance(err, GraphError):
        return err.to_user_message()
    return str(err)


def _messaging(cfg: IgConfig, args: dict) -> dict[str, Any]:
    if cfg.login_kind == "fb_login":
        actor = (args.get("ig_user_id") or "").strip() or cfg.page_id
        token = (args.get("access_token") or "").strip() or cfg.page_access_token or cfg.access_token
        if not actor:
            return {"error": "Messaging via Facebook Login needs FB_PAGE_ID set."}
        return {"kind": "fb_login", "actor": actor, "token": token}
    token, user_id = resolve(cfg, args)
    if not user_id:
        return {"error": "Messaging needs IG_USER_ID set."}
    return {"kind": "ig_login", "actor": user_id, "token": token}


async def execute_tool(name: str, args: dict, cfg: IgConfig, client: InstagramClient) -> str:
    """Execute an Instagram tool and return the result as a JSON string."""
    try:
        token, user_id = resolve(cfg, {})

        if name == "get_profile_info":
            if not user_id:
                return json.dumps({"error": "No IG_USER_ID configured."})
            fields = args.get("fields") or (
                "username,name,biography,website,followers_count,"
                "follows_count,media_count,profile_picture_url"
            )
            data = await client.get(user_id, {"fields": fields}, {"token": token})
            return json.dumps(data, indent=2)

        elif name == "get_media_posts":
            if not user_id:
                return json.dumps({"error": "No IG_USER_ID configured."})
            fields = args.get("fields") or (
                "id,caption,media_type,media_url,thumbnail_url,"
                "permalink,timestamp,like_count,comments_count"
            )
            params: dict[str, Any] = {"fields": fields, "limit": args.get("limit", 25)}
            if args.get("after"):
                params["after"] = args["after"]
            data = await client.get(f"{user_id}/media", params, {"token": token})
            return json.dumps(data, indent=2)

        elif name == "get_media_insights":
            metric = args.get("metrics") or "reach,likes,comments,saved,shares"
            data = await client.get(
                f"{args['media_id']}/insights", {"metric": metric}, {"token": token}
            )
            return json.dumps(data, indent=2)

        elif name == "publish_media":
            if not user_id:
                return json.dumps({"error": "No IG_USER_ID configured."})
            ctx = PublishCtx(client=client, user_id=user_id, token=token, kind=cfg.login_kind)
            media_type = args.get("media_type")
            carousel_items = args.get("carousel_items")
            image_url = args.get("image_url")
            video_url = args.get("video_url")
            caption = args.get("caption")

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
                    return json.dumps({"error": "carousel_items required for carousel."})
                outcome = await publish_carousel(ctx, carousel_items, caption)
            elif media_type == "story":
                outcome = await publish_story(ctx, image_url=image_url, video_url=video_url)
            elif media_type == "reel" or (video_url and media_type != "image"):
                if not video_url:
                    return json.dumps({"error": "video_url required for reel."})
                outcome = await publish_reel(
                    ctx, video_url, caption,
                    share_to_feed=args.get("share_to_feed"),
                    cover_url=args.get("cover_url"),
                )
            elif image_url:
                outcome = await publish_image(ctx, image_url, caption)
            else:
                return json.dumps({"error": "Provide image_url, video_url, or carousel_items."})
            return json.dumps({"status": "published", **outcome.to_dict()}, indent=2)

        elif name == "publish_local_media":
            if not user_id:
                return json.dumps({"error": "No IG_USER_ID configured."})

            local_path = args.get("local_path", "")
            if not local_path:
                return json.dumps({"error": "local_path is required."})

            detected = detect_media_type(local_path)
            effective_type = args.get("media_type") or ("reel" if detected == "video" else "image")

            import asyncio as _aio
            uploader = get_google_drive_uploader()
            loop = _aio.get_event_loop()
            public_url, file_id = await loop.run_in_executor(None, uploader.upload_file, local_path)

            ctx = PublishCtx(client=client, user_id=user_id, token=token, kind=cfg.login_kind)
            caption = args.get("caption")

            if effective_type == "story":
                if detected == "video":
                    outcome = await publish_story(ctx, video_url=public_url)
                else:
                    outcome = await publish_story(ctx, image_url=public_url)
            elif effective_type == "reel":
                outcome = await publish_reel(
                    ctx, public_url, caption,
                    share_to_feed=args.get("share_to_feed"),
                    cover_url=args.get("cover_url"),
                )
            else:
                outcome = await publish_image(ctx, public_url, caption)

            await loop.run_in_executor(None, uploader.delete_file, file_id)
            return json.dumps({"status": "published", "local_path": local_path, **outcome.to_dict()}, indent=2)

        elif name == "schedule_post":
            if not user_id:
                return json.dumps({"error": "No IG_USER_ID configured."})

            from datetime import datetime as dt, timezone

            scheduled_time_str = args.get("scheduled_time", "")
            try:
                publish_time = dt.fromisoformat(scheduled_time_str)
            except ValueError:
                return json.dumps({"error": f"Invalid datetime: '{scheduled_time_str}'. Use ISO 8601."})

            local_path = args.get("local_path")
            image_url = args.get("image_url")
            video_url = args.get("video_url")
            caption = args.get("caption")
            is_local = False

            if local_path:
                media_source = local_path
                is_local = True
                detected = detect_media_type(local_path)
                effective_type = args.get("media_type") or ("reel" if detected == "video" else "image")
            elif video_url:
                media_source = video_url
                effective_type = args.get("media_type") or "reel"
            elif image_url:
                media_source = image_url
                effective_type = args.get("media_type") or "image"
            else:
                return json.dumps({"error": "Provide image_url, video_url, or local_path."})

            async def _do_publish(post: ScheduledPost) -> dict:
                import asyncio as _aio2

                _token, _uid = resolve(cfg, {})
                _ctx = PublishCtx(client=ig_client, user_id=_uid, token=_token, kind=cfg.login_kind)
                src = post.media_source

                if post.is_local:
                    _uploader = get_google_drive_uploader()
                    _loop = _aio2.get_event_loop()
                    src, _file_id = await _loop.run_in_executor(None, _uploader.upload_file, post.media_source)

                if post.media_type == "story":
                    det = detect_media_type(post.media_source) if post.is_local else (
                        "video" if post.media_source.endswith((".mp4", ".mov")) else "image"
                    )
                    if det == "video":
                        o = await publish_story(_ctx, video_url=src)
                    else:
                        o = await publish_story(_ctx, image_url=src)
                elif post.media_type == "reel":
                    o = await publish_reel(
                        _ctx, src, post.caption,
                        share_to_feed=post.share_to_feed, cover_url=post.cover_url,
                    )
                else:
                    o = await publish_image(_ctx, src, post.caption)
                return {"status": "published", **o.to_dict()}

            scheduler = get_post_scheduler()
            job_id = scheduler.schedule_post(
                scheduled_time=publish_time,
                media_type=effective_type,
                media_source=media_source,
                publish_callback=_do_publish,
                caption=caption,
                share_to_feed=args.get("share_to_feed"),
                cover_url=args.get("cover_url"),
                is_local=is_local,
            )
            return json.dumps({
                "status": "scheduled", "job_id": job_id,
                "scheduled_time": publish_time.isoformat(),
                "media_type": effective_type,
                "media_source": media_source,
                "caption": caption,
            }, indent=2)

        elif name == "list_scheduled_posts":
            scheduler = get_post_scheduler()
            posts = scheduler.list_scheduled(include_completed=args.get("include_completed", True))
            if not posts:
                return json.dumps({"message": "No scheduled posts found.", "posts": []})
            return json.dumps({"count": len(posts), "posts": posts}, indent=2)

        elif name == "cancel_scheduled_post":
            scheduler = get_post_scheduler()
            job_id = args.get("job_id", "")
            success = scheduler.cancel_scheduled(job_id)
            if success:
                return json.dumps({"status": "cancelled", "job_id": job_id})
            post = scheduler.get_post(job_id)
            if post:
                return json.dumps({"error": f"Cannot cancel — status is '{post['status']}'.", "post": post})
            return json.dumps({"error": f"No scheduled post found with ID '{job_id}'."})

        elif name == "get_account_pages":
            if cfg.login_kind != "fb_login":
                return json.dumps({"error": "Requires IG_LOGIN_KIND=fb_login."})
            data = await client.get(
                "me/accounts",
                {"fields": "id,name,access_token,instagram_business_account{id,username}"},
                {"token": token, "kind": "fb_login"},
            )
            return json.dumps(data, indent=2)

        elif name == "get_conversations":
            m = _messaging(cfg, {})
            if "error" in m:
                return json.dumps({"error": m["error"]})
            data = await client.get(
                f"{m['actor']}/conversations",
                {"platform": "instagram", "fields": "id,updated_time,participants", "limit": args.get("limit", 20)},
                {"token": m["token"], "kind": m["kind"]},
            )
            return json.dumps(data, indent=2)

        elif name == "get_conversation_messages":
            m = _messaging(cfg, {})
            if "error" in m:
                return json.dumps({"error": m["error"]})
            data = await client.get(
                f"{args['conversation_id']}/messages",
                {"fields": "id,created_time,from,to,message", "limit": args.get("limit", 25)},
                {"token": m["token"], "kind": m["kind"]},
            )
            return json.dumps(data, indent=2)

        elif name == "send_dm":
            m = _messaging(cfg, {})
            if "error" in m:
                return json.dumps({"error": m["error"]})
            body = {"recipient": {"id": args["recipient_id"]}, "message": {"text": args["message"]}}
            data = await client.post_json(
                f"{m['actor']}/messages", body, {"token": m["token"], "kind": m["kind"]}
            )
            return json.dumps(data, indent=2)

        else:
            return json.dumps({"error": f"Unknown tool: {name}"})

    except Exception as e:
        return json.dumps({"error": _err_text(e)})


# ──────────────────────────────────────────────────────────────────────────────
# Main conversation loop
# ──────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an Instagram assistant powered by OpenInstaFlow. You can manage an Instagram \
Business/Creator account using the available tools. You can:
- Read profile info, recent posts, and engagement insights
- Publish images, reels, stories, and carousels (media must be public HTTPS URLs — Google \
Drive share links like drive.google.com/file/d/.../view are automatically converted to direct \
download URLs, so the user can paste Drive share links directly)
- Publish LOCAL files (images/videos) from the user's computer using publish_local_media \
(the file is uploaded to Google Drive and made publicly accessible for Instagram to fetch)
- Schedule posts for future publishing with schedule_post (use ISO 8601 datetime, e.g. \
'2026-06-08T15:00:00+05:30'). List scheduled posts with list_scheduled_posts and cancel \
them with cancel_scheduled_post
- List Facebook Pages connected to the account
- Read and send Instagram DMs (requires Advanced Access)

When the user wants to publish a local file, use publish_local_media with the full file path. \
When the user wants to schedule a post, use schedule_post with a future datetime. The user's \
timezone is IST (UTC+5:30) — interpret times accordingly unless they specify otherwise.

Always explain what you're about to do before calling a tool. After getting results, \
summarize them in a clear, helpful way. If something fails, explain the error and \
suggest how to fix it.\
"""


import re
import os
import base64
from openinstaflow.local_media import is_gdrive_url, convert_gdrive_url

def parse_vision_content(text: str) -> list[dict[str, Any]] | str:
    """Extract URLs and local paths from text and format for OpenAI Vision API."""
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    added_image = False
    
    # 1. Look for URLs
    urls = re.findall(r'https?://[^\s]+', text)
    for url in urls:
        ext = url.split('?')[0].lower()
        if ext.endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif')) or is_gdrive_url(url):
            direct_url = convert_gdrive_url(url)
            content.append({"type": "image_url", "image_url": {"url": direct_url}})
            added_image = True
            
    # 2. Look for local paths by splitting text and checking if valid file
    # We do a simple split and check. Words might have punctuation so we strip it.
    for word in text.split():
        path = word.strip('"\',;')
        if os.path.isfile(path):
            ext = os.path.splitext(path)[1].lower()
            if ext in {'.jpg', '.jpeg', '.png', '.webp', '.gif'}:
                try:
                    with open(path, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode('utf-8')
                    mime = "image/jpeg" if ext in {'.jpg', '.jpeg'} else f"image/{ext[1:]}"
                    content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
                    added_image = True
                except Exception:
                    pass

    return content if added_image else text


async def run_conversation(openai_client: OpenAI, cfg: IgConfig, prompt: str | None = None) -> None:
    """Run an interactive (or one-shot) conversation with tool calling."""
    ig_client = InstagramClient(cfg)
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]

    print("\n╔══════════════════════════════════════════════════════════════╗")
    print("║        OpenInstaFlow × OpenAI — Instagram Assistant        ║")
    print("╠══════════════════════════════════════════════════════════════╣")
    print("║  Type your request, or 'quit' to exit.                     ║")
    print("║  The assistant can read your profile, posts, insights,     ║")
    print("║  publish media (URLs or local files), schedule posts,      ║")
    print("║  and manage DMs.                                           ║")
    print("╚══════════════════════════════════════════════════════════════╝\n")

    one_shot = prompt is not None

    while True:
        if prompt is None:
            try:
                user_input = input("\033[1;36mYou:\033[0m ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye!")
                break
            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                print("Goodbye!")
                break
        else:
            user_input = prompt
            print(f"\033[1;36mYou:\033[0m {user_input}")
            prompt = None  # only use once

        messages.append({"role": "user", "content": parse_vision_content(user_input)})

        # Loop to handle multi-turn tool calls
        while True:
            response = openai_client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
            )

            choice = response.choices[0]
            assistant_msg = choice.message

            # If the model wants to call tools
            if assistant_msg.tool_calls:
                messages.append(assistant_msg.model_dump())

                for tool_call in assistant_msg.tool_calls:
                    fn_name = tool_call.function.name
                    fn_args = json.loads(tool_call.function.arguments)

                    print(f"\033[1;33m⚡ Calling tool:\033[0m {fn_name}({json.dumps(fn_args, indent=2)})")

                    result = await execute_tool(fn_name, fn_args, cfg, ig_client)

                    print(f"\033[1;32m✓ Result:\033[0m {result[:200]}{'...' if len(result) > 200 else ''}\n")

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    })

                # Continue the loop so the model can process tool results
                continue

            # No tool calls — model has a final text response
            final_text = assistant_msg.content or ""
            messages.append({"role": "assistant", "content": final_text})
            print(f"\n\033[1;35mAssistant:\033[0m {final_text}\n")
            break

        if one_shot:
            break


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="OpenAI ↔ OpenInstaFlow bridge")
    parser.add_argument("--prompt", "-p", type=str, default=None, help="One-shot prompt (skip interactive mode)")
    parser.add_argument("--model", "-m", type=str, default="gpt-4o", help="OpenAI model (default: gpt-4o)")
    args = parser.parse_args()

    # Validate keys
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set. Add it to .env or export it.", file=sys.stderr)
        sys.exit(1)

    cfg = load_config()
    if not cfg.access_token:
        print(
            "WARNING: IG_ACCESS_TOKEN not set. Tools will fail until a token is provided.",
            file=sys.stderr,
        )

    openai_client = OpenAI(api_key=api_key)
    try:
        asyncio.run(run_conversation(openai_client, cfg, prompt=args.prompt))
    finally:
        # Graceful shutdown of background services
        shutdown_google_drive_uploader()
        shutdown_post_scheduler()


if __name__ == "__main__":
    main()
