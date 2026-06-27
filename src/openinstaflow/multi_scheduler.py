"""
Multi-tenant post scheduler with database persistence.

Unlike the original in-memory scheduler, this version:
  - Stores every job in the ``posts`` table (survives restarts)
  - Re-schedules pending posts on startup
  - Routes each publish through the correct customer's credentials
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .client import InstagramClient
from .config import IgConfig
from .database import ActivityLog, Customer, Post, decrypt_token, get_db, log_activity
from .local_media import detect_media_type, get_google_drive_uploader
from .publish import PublishCtx, publish_carousel, publish_image, publish_reel, publish_story


class MultiTenantScheduler:
    """
    Persistent, multi-tenant post scheduler.

    Each scheduled post is stored in the DB and a corresponding APScheduler
    job is created. On startup, all ``pending`` posts whose time hasn't passed
    are re-loaded from the DB.
    """

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler(timezone=timezone.utc)
        self._started = False

    @property
    def is_running(self) -> bool:
        return self._started

    def start(self) -> None:
        """Start the scheduler and reload pending jobs from DB."""
        if not self._started:
            self._scheduler.start()
            self._started = True
            self._reload_pending_posts()
            self._scheduler.add_job(
                self._run_autopilot_tick,
                trigger=IntervalTrigger(hours=6),
                id="autopilot_tick",
                name="Growth agent autopilot tick",
                replace_existing=True,
                next_run_time=datetime.now(timezone.utc) + timedelta(minutes=1),
            )
            print("[OpenInstaFlow] Multi-tenant scheduler started.", file=sys.stderr)

    async def _run_autopilot_tick(self) -> None:
        """Periodic job: let the growth agent plan the next post for every autopilot customer."""
        from .growth_agent import run_autopilot_tick

        try:
            await run_autopilot_tick()
        except Exception as e:
            print(f"[Scheduler] Autopilot tick failed: {e}", file=sys.stderr)

    def schedule_existing_post(self, post_id: str, scheduled_time: datetime) -> None:
        """Register an APScheduler job for a Post row that already exists in the DB

        (e.g. an approved draft, or a pending post created directly by the growth agent).
        """
        if not self._started:
            self.start()
        if scheduled_time.tzinfo is None:
            scheduled_time = scheduled_time.replace(tzinfo=timezone.utc)
        self._add_job(post_id, scheduled_time)

    def shutdown(self) -> None:
        """Shut down gracefully."""
        if self._started:
            self._scheduler.shutdown(wait=False)
            self._started = False

    def _reload_pending_posts(self) -> None:
        """Reload pending posts from the database and re-schedule them."""
        db = get_db()
        try:
            now = datetime.now(timezone.utc)
            pending = (
                db.query(Post)
                .filter(Post.status == "pending", Post.scheduled_time > now)
                .all()
            )
            for post in pending:
                try:
                    self._add_job(post.id, post.scheduled_time)
                    print(
                        f"[Scheduler] Re-loaded post {post.id} for {post.scheduled_time.isoformat()}",
                        file=sys.stderr,
                    )
                except Exception as e:
                    print(f"[Scheduler] Failed to reload post {post.id}: {e}", file=sys.stderr)

            # Mark any past-due pending posts as failed
            overdue = (
                db.query(Post)
                .filter(Post.status == "pending", Post.scheduled_time <= now)
                .all()
            )
            for post in overdue:
                post.status = "failed"
                post.error = "Missed scheduled time (server was down)"
            if overdue:
                db.commit()
                print(f"[Scheduler] Marked {len(overdue)} overdue posts as failed.", file=sys.stderr)
        finally:
            db.close()

    def schedule_post(
        self,
        customer_id: str,
        media_type: str,
        media_source: str,
        scheduled_time: datetime,
        caption: Optional[str] = None,
        share_to_feed: Optional[bool] = None,
        cover_url: Optional[str] = None,
        is_local: bool = False,
    ) -> Post:
        """
        Schedule a post for a customer.

        Creates a DB record and an APScheduler job.
        Returns the created Post object.
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

        # Create DB record
        db = get_db()
        try:
            post = Post(
                customer_id=customer_id,
                media_type=media_type,
                media_source=media_source,
                caption=caption,
                status="pending",
                scheduled_time=scheduled_time,
                share_to_feed=share_to_feed,
                cover_url=cover_url,
                is_local=is_local,
            )
            db.add(post)
            db.commit()
            db.refresh(post)

            log_activity(db, customer_id, "post_scheduled", f"Post {post.id} scheduled for {scheduled_time.isoformat()}")

            # Add APScheduler job
            self._add_job(post.id, scheduled_time)

            # log_activity's own commit re-expires session objects; refresh once more (including
            # the lazy-loaded customer relationship, used by Post.to_dict()) so the returned
            # instance stays readable after this function's session closes below.
            db.refresh(post)
            _ = post.customer
            return post
        finally:
            db.close()

    def _add_job(self, post_id: str, run_date: datetime) -> None:
        """Add an APScheduler job for a post."""
        self._scheduler.add_job(
            self._execute_post,
            trigger=DateTrigger(run_date=run_date),
            args=[post_id],
            id=post_id,
            name=f"Post {post_id}",
            misfire_grace_time=300,
            replace_existing=True,
        )

    async def _execute_post(self, post_id: str) -> None:
        """Execute a scheduled post — called by APScheduler."""
        db = get_db()
        try:
            post = db.query(Post).filter(Post.id == post_id).first()
            if not post or post.status != "pending":
                return

            customer = db.query(Customer).filter(Customer.id == post.customer_id).first()
            if not customer or customer.status != "active":
                post.status = "failed"
                post.error = "Customer account is not active"
                db.commit()
                return

            post.status = "publishing"
            db.commit()

            try:
                # Build config from customer credentials
                token = customer.ig_access_token
                user_id = customer.ig_user_id
                login_kind = customer.login_kind or "ig_login"

                cfg = IgConfig(
                    access_token=token,
                    ig_user_id=user_id,
                    login_kind=login_kind,
                )
                client = InstagramClient(cfg)
                ctx = PublishCtx(client=client, user_id=user_id, token=token, kind=login_kind)

                src = post.media_source

                # Handle local file upload
                if post.is_local:
                    uploader = get_google_drive_uploader()
                    loop = asyncio.get_event_loop()
                    src, file_id = await loop.run_in_executor(None, uploader.upload_file, post.media_source)

                # Publish based on media type
                if post.media_type == "story":
                    detected = "video" if src.endswith((".mp4", ".mov")) else "image"
                    if detected == "video":
                        outcome = await publish_story(ctx, video_url=src)
                    else:
                        outcome = await publish_story(ctx, image_url=src)
                elif post.media_type == "reel":
                    outcome = await publish_reel(
                        ctx, src, post.caption,
                        share_to_feed=post.share_to_feed,
                        cover_url=post.cover_url,
                    )
                else:  # image
                    outcome = await publish_image(ctx, src, post.caption)

                post.status = "published"
                post.ig_media_id = outcome.media_id
                post.permalink = outcome.permalink
                post.published_at = datetime.now(timezone.utc)
                db.commit()

                log_activity(db, post.customer_id, "post_published", f"Post {post.id} published: {outcome.permalink or outcome.media_id}")

                # Clean up Google Drive file if local
                if post.is_local:
                    try:
                        await loop.run_in_executor(None, uploader.delete_file, file_id)
                    except Exception:
                        pass

            except Exception as e:
                post.status = "failed"
                post.error = str(e)
                db.commit()
                log_activity(db, post.customer_id, "post_failed", f"Post {post.id} failed: {e}")

        finally:
            db.close()

    def cancel_post(self, post_id: str) -> bool:
        """Cancel a pending post."""
        db = get_db()
        try:
            post = db.query(Post).filter(Post.id == post_id).first()
            if not post or post.status != "pending":
                return False

            post.status = "cancelled"
            db.commit()

            try:
                self._scheduler.remove_job(post_id)
            except Exception:
                pass

            log_activity(db, post.customer_id, "post_cancelled", f"Post {post.id} cancelled")
            return True
        finally:
            db.close()


# ──────────────────────────────────────────────────────────────────────────────
# Module-level singleton
# ──────────────────────────────────────────────────────────────────────────────

_mt_scheduler: Optional[MultiTenantScheduler] = None


def get_mt_scheduler() -> MultiTenantScheduler:
    """Get or create the singleton multi-tenant scheduler."""
    global _mt_scheduler
    if _mt_scheduler is None:
        _mt_scheduler = MultiTenantScheduler()
        _mt_scheduler.start()
    return _mt_scheduler


def shutdown_mt_scheduler() -> None:
    """Shutdown the multi-tenant scheduler."""
    global _mt_scheduler
    if _mt_scheduler:
        _mt_scheduler.shutdown()
    _mt_scheduler = None
