"""
Post scheduler for Instagram publishing.

Uses APScheduler to queue one-off publish jobs for a future datetime.
Each job stores publish parameters and executes the appropriate publish_*
function when the scheduled time arrives.

Jobs are stored in-memory (lost on restart). Each job has a unique ID and
tracks its status: pending → published | failed.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger


@dataclass
class ScheduledPost:
    """Metadata for a scheduled post."""

    job_id: str
    scheduled_time: datetime
    media_type: str  # image, reel, story, carousel
    media_source: str  # URL or local path
    caption: Optional[str] = None
    status: str = "pending"  # pending, publishing, published, failed
    error: Optional[str] = None
    result: Optional[dict[str, Any]] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Extra publish kwargs
    share_to_feed: Optional[bool] = None
    cover_url: Optional[str] = None
    carousel_items: Optional[list[dict[str, str]]] = None
    is_local: bool = False  # True if media_source is a local file path

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "job_id": self.job_id,
            "scheduled_time": self.scheduled_time.isoformat(),
            "media_type": self.media_type,
            "media_source": self.media_source,
            "caption": self.caption,
            "status": self.status,
            "is_local": self.is_local,
            "created_at": self.created_at.isoformat(),
        }
        if self.error:
            d["error"] = self.error
        if self.result:
            d["result"] = self.result
        if self.share_to_feed is not None:
            d["share_to_feed"] = self.share_to_feed
        if self.cover_url:
            d["cover_url"] = self.cover_url
        if self.carousel_items:
            d["carousel_items"] = self.carousel_items
        return d


# Type alias for the async publish callback
PublishCallback = Callable[[ScheduledPost], Coroutine[Any, Any, dict[str, Any]]]


class PostScheduler:
    """
    In-memory post scheduler backed by APScheduler.

    Usage::

        scheduler = PostScheduler()
        scheduler.start()

        job_id = scheduler.schedule_post(
            scheduled_time=datetime(2026, 6, 8, 15, 0, tzinfo=timezone.utc),
            media_type="image",
            media_source="https://example.com/photo.jpg",
            caption="Hello world!",
            publish_callback=my_publish_func,
        )

        posts = scheduler.list_scheduled()
        scheduler.cancel_scheduled(job_id)
        scheduler.shutdown()
    """

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()
        self._posts: dict[str, ScheduledPost] = {}
        self._callbacks: dict[str, PublishCallback] = {}
        self._started = False

    @property
    def is_running(self) -> bool:
        return self._started

    def start(self) -> None:
        """Start the background scheduler."""
        if not self._started:
            self._scheduler.start()
            self._started = True

    def shutdown(self) -> None:
        """Shut down the scheduler gracefully."""
        if self._started:
            self._scheduler.shutdown(wait=False)
            self._started = False

    def schedule_post(
        self,
        scheduled_time: datetime,
        media_type: str,
        media_source: str,
        publish_callback: PublishCallback,
        caption: Optional[str] = None,
        share_to_feed: Optional[bool] = None,
        cover_url: Optional[str] = None,
        carousel_items: Optional[list[dict[str, str]]] = None,
        is_local: bool = False,
    ) -> str:
        """
        Schedule a post for future publishing.

        Args:
            scheduled_time: When to publish (must be timezone-aware or treated as UTC).
            media_type: One of 'image', 'reel', 'story', 'carousel'.
            media_source: Public URL or local file path.
            publish_callback: Async function that actually publishes the post.
            caption: Caption text.
            share_to_feed: Reels only — show on main feed.
            cover_url: Reels only — cover image URL.
            carousel_items: Carousel items list.
            is_local: True if media_source is a local path.

        Returns:
            The generated job ID.
        """
        if not self._started:
            self.start()

        # Ensure timezone-aware
        if scheduled_time.tzinfo is None:
            scheduled_time = scheduled_time.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        if scheduled_time <= now:
            raise ValueError(
                f"Scheduled time must be in the future. "
                f"Got: {scheduled_time.isoformat()}, now: {now.isoformat()}"
            )

        job_id = f"post_{uuid.uuid4().hex[:8]}"
        post = ScheduledPost(
            job_id=job_id,
            scheduled_time=scheduled_time,
            media_type=media_type,
            media_source=media_source,
            caption=caption,
            share_to_feed=share_to_feed,
            cover_url=cover_url,
            carousel_items=carousel_items,
            is_local=is_local,
        )

        self._posts[job_id] = post
        self._callbacks[job_id] = publish_callback

        # Schedule the job
        self._scheduler.add_job(
            self._execute_post,
            trigger=DateTrigger(run_date=scheduled_time),
            args=[job_id],
            id=job_id,
            name=f"Instagram post: {media_type} at {scheduled_time.isoformat()}",
            misfire_grace_time=300,  # 5 min grace if slightly late
        )

        return job_id

    async def _execute_post(self, job_id: str) -> None:
        """Internal: called by APScheduler when a job fires."""
        post = self._posts.get(job_id)
        callback = self._callbacks.get(job_id)
        if not post or not callback:
            return

        post.status = "publishing"
        try:
            result = await callback(post)
            post.status = "published"
            post.result = result
        except Exception as e:
            post.status = "failed"
            post.error = str(e)

    def list_scheduled(self, include_completed: bool = True) -> list[dict[str, Any]]:
        """
        List all scheduled posts.

        Args:
            include_completed: If True, includes published/failed posts too.
        """
        posts = []
        for post in self._posts.values():
            if not include_completed and post.status in ("published", "failed"):
                continue
            posts.append(post.to_dict())
        # Sort by scheduled time
        posts.sort(key=lambda p: p["scheduled_time"])
        return posts

    def cancel_scheduled(self, job_id: str) -> bool:
        """
        Cancel a pending scheduled post.

        Returns True if successfully cancelled, False if not found or already executed.
        """
        post = self._posts.get(job_id)
        if not post:
            return False
        if post.status != "pending":
            return False

        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass  # job may have already fired

        post.status = "cancelled"
        self._callbacks.pop(job_id, None)
        return True

    def get_post(self, job_id: str) -> Optional[dict[str, Any]]:
        """Get a specific scheduled post by ID."""
        post = self._posts.get(job_id)
        return post.to_dict() if post else None


# ──────────────────────────────────────────────────────────────────────────────
# Module-level singleton
# ──────────────────────────────────────────────────────────────────────────────

_scheduler: Optional[PostScheduler] = None


def get_post_scheduler() -> PostScheduler:
    """Get or create the singleton PostScheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = PostScheduler()
        _scheduler.start()
    return _scheduler


def shutdown_post_scheduler() -> None:
    """Shut down the singleton scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown()
    _scheduler = None
