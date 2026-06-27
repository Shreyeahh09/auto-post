"""
Growth agent: reasons about a customer's audience/location/behavior and decides what
and when to post next.

``analyze_account`` pulls recent media performance (and, best-effort, Graph audience
demographics) to figure out good posting times and locations, falling back to the
customer's manually entered ``AutopilotSettings`` when Graph denies access (common for
accounts without Advanced Access or enough followers).

``plan_next_post`` consumes the next queued ``MediaAsset``, asks ``ai_caption`` for a
caption grounded in the account's niche/tone/goal/location, and creates a ``Post`` —
either ``pending`` (auto-publish enabled) or ``draft`` (awaiting customer approval).
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from . import ai_caption
from .client import InstagramClient
from .config import IgConfig
from .database import AutopilotSettings, Customer, MediaAsset, Post, StrategyInsight, get_db, log_activity

DEFAULT_HOURS = [9, 13, 19]
DEFAULT_DAYS = [0, 2, 4]  # Mon, Wed, Fri


def _parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z")


def _compute_best_times(items: list[dict], tz: ZoneInfo) -> tuple[list[int], list[int]]:
    hour_scores: dict[int, list[int]] = defaultdict(list)
    day_scores: dict[int, list[int]] = defaultdict(list)
    for item in items:
        ts = item.get("timestamp")
        if not ts:
            continue
        try:
            dt = _parse_ts(ts).astimezone(tz)
        except ValueError:
            continue
        score = (item.get("like_count") or 0) + (item.get("comments_count") or 0)
        hour_scores[dt.hour].append(score)
        day_scores[dt.weekday()].append(score)

    if not hour_scores:
        return list(DEFAULT_HOURS), list(DEFAULT_DAYS)

    ranked_hours = sorted(hour_scores, key=lambda h: sum(hour_scores[h]) / len(hour_scores[h]), reverse=True)
    ranked_days = sorted(day_scores, key=lambda d: sum(day_scores[d]) / len(day_scores[d]), reverse=True)
    return ranked_hours[:3] or list(DEFAULT_HOURS), ranked_days[:3] or list(DEFAULT_DAYS)


def _extract_demographics(resp: Any) -> Optional[dict]:
    """Best-effort parse of a follower_demographics insights response."""
    try:
        data = resp.get("data", []) if isinstance(resp, dict) else []
        if not data:
            return None
        breakdowns = data[0].get("total_value", {}).get("breakdowns", [])
        if not breakdowns:
            return None
        results = breakdowns[0].get("results", [])
        out: dict[str, Any] = {}
        for r in results[:5]:
            dims = r.get("dimension_values") or []
            if dims and dims[0]:
                out[dims[0]] = r.get("value")
        return out or None
    except Exception:
        return None


def _build_summary(best_hours: list[int], best_days: list[int], audience_locations: Optional[dict]) -> str:
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    days_str = ", ".join(day_names[d] for d in best_days if 0 <= d <= 6)
    hours_str = ", ".join(f"{h:02d}:00" for h in best_hours)
    summary = f"Best posting times: {days_str} around {hours_str}."
    if audience_locations:
        top = list(audience_locations.items())[:3]
        summary += " Top audience locations: " + ", ".join(f"{k} ({v})" for k, v in top) + "."
    return summary


async def analyze_account(customer_id: str) -> StrategyInsight:
    """Analyze a customer's account and persist a fresh StrategyInsight."""
    db = get_db()
    try:
        customer = db.query(Customer).filter(Customer.id == customer_id).first()
        if not customer:
            raise ValueError(f"Customer not found: {customer_id}")

        settings = db.query(AutopilotSettings).filter_by(customer_id=customer.id).first()
        tz = ZoneInfo(settings.timezone) if settings and settings.timezone else ZoneInfo("UTC")

        best_hours, best_days = list(DEFAULT_HOURS), list(DEFAULT_DAYS)
        source = "manual"

        if customer.ig_access_token and customer.ig_user_id:
            cfg = IgConfig(
                access_token=customer.ig_access_token,
                ig_user_id=customer.ig_user_id,
                login_kind=customer.login_kind or "ig_login",
            )
            client = InstagramClient(cfg)

            try:
                media_data = await client.get(
                    f"{customer.ig_user_id}/media",
                    {"fields": "id,timestamp,like_count,comments_count", "limit": 50},
                    {"token": customer.ig_access_token},
                )
                items = media_data.get("data", []) if isinstance(media_data, dict) else []
                if items:
                    best_hours, best_days = _compute_best_times(items, tz)
                    source = "graph"
            except Exception:
                pass  # keep manual defaults

            audience_locations = None
            try:
                demo = await client.get(
                    f"{customer.ig_user_id}/insights",
                    {
                        "metric": "follower_demographics",
                        "period": "lifetime",
                        "metric_type": "total_value",
                        "breakdown": "country",
                    },
                    {"token": customer.ig_access_token},
                )
                audience_locations = _extract_demographics(demo)
                if audience_locations:
                    source = "mixed" if source == "graph" else "graph"
            except Exception:
                audience_locations = None
        else:
            audience_locations = None

        if not audience_locations and settings and settings.target_location:
            audience_locations = {"manual": settings.target_location}
            source = "mixed" if source == "graph" else "manual"

        insight = StrategyInsight(
            customer_id=customer.id,
            best_hours=json.dumps(best_hours),
            best_days=json.dumps(best_days),
            audience_locations=json.dumps(audience_locations) if audience_locations else None,
            summary=_build_summary(best_hours, best_days, audience_locations),
            source=source,
        )
        db.add(insight)
        db.commit()
        db.refresh(insight)
        log_activity(db, customer.id, "strategy_refreshed", insight.summary)
        # log_activity's own commit re-expires session objects; refresh once more so the
        # returned instance stays readable after this function's session closes below.
        db.refresh(insight)
        return insight
    finally:
        db.close()


async def _get_last_post_time(customer: Customer) -> Optional[datetime]:
    """Best-effort fetch of the actual timestamp of the customer's most recent IG post.

    Checking Graph directly (rather than only our own ``last_planned_at``) means the cadence
    still holds even if the customer also posts manually outside the app.
    """
    if not customer.ig_access_token or not customer.ig_user_id:
        return None
    cfg = IgConfig(
        access_token=customer.ig_access_token,
        ig_user_id=customer.ig_user_id,
        login_kind=customer.login_kind or "ig_login",
    )
    client = InstagramClient(cfg)
    try:
        media_data = await client.get(
            f"{customer.ig_user_id}/media",
            {"fields": "timestamp", "limit": 1},
            {"token": customer.ig_access_token},
        )
        items = media_data.get("data", []) if isinstance(media_data, dict) else []
        if items and items[0].get("timestamp"):
            return _parse_ts(items[0]["timestamp"])
    except Exception:
        pass  # fall back to our own record below
    return None


async def _is_due(customer: Customer, settings: AutopilotSettings) -> bool:
    posts_per_week = max(settings.posts_per_week or 1, 1)
    interval = timedelta(days=7 / posts_per_week)

    last = await _get_last_post_time(customer)
    if last is None:
        last = settings.last_planned_at
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last >= interval


def _next_slot(best_hours: list[int], tz: ZoneInfo) -> datetime:
    hours = sorted(set(best_hours)) or list(DEFAULT_HOURS)
    now_local = datetime.now(tz)
    for h in hours:
        candidate = now_local.replace(hour=h, minute=0, second=0, microsecond=0)
        if candidate > now_local + timedelta(minutes=15):
            return candidate.astimezone(timezone.utc)
    candidate = (now_local + timedelta(days=1)).replace(hour=hours[0], minute=0, second=0, microsecond=0)
    return candidate.astimezone(timezone.utc)


async def plan_next_post(customer_id: str) -> Optional[Post]:
    """Plan and create the next autopilot post for a customer, if one is due."""
    db = get_db()
    try:
        customer = db.query(Customer).filter(Customer.id == customer_id).first()
        if not customer or customer.status != "active":
            return None

        settings = db.query(AutopilotSettings).filter_by(customer_id=customer.id).first()
        if not settings or not settings.enabled:
            return None
        if not await _is_due(customer, settings):
            return None

        asset = (
            db.query(MediaAsset)
            .filter(MediaAsset.customer_id == customer.id, MediaAsset.status == "queued")
            .order_by(MediaAsset.created_at.asc())
            .first()
        )
        if not asset:
            log_activity(db, customer.id, "autopilot_no_media", "Autopilot is due but the media queue is empty.")
            return None

        insight = (
            db.query(StrategyInsight)
            .filter_by(customer_id=customer.id)
            .order_by(StrategyInsight.generated_at.desc())
            .first()
        )
        if not insight or (datetime.now(timezone.utc) - insight.generated_at.replace(tzinfo=timezone.utc)) > timedelta(days=7):
            insight = await analyze_account(customer.id)

        best_hours = json.loads(insight.best_hours) if insight and insight.best_hours else (
            json.loads(settings.preferred_hours) if settings.preferred_hours else list(DEFAULT_HOURS)
        )
        tz = ZoneInfo(settings.timezone) if settings.timezone else ZoneInfo("UTC")
        scheduled_time = _next_slot(best_hours, tz)

        try:
            caption = await ai_caption.generate_caption(
                image_url=asset.url if asset.media_type == "image" else None,
                niche=settings.niche,
                tone=settings.tone,
                goal=settings.goal,
                location=settings.target_location,
                caption_hint=asset.caption_hint,
            )
        except Exception as e:
            caption = asset.caption_hint or ""
            log_activity(db, customer.id, "ai_caption_failed", str(e))

        post_status = "pending" if settings.auto_publish else "draft"
        post = Post(
            customer_id=customer.id,
            media_type="reel" if asset.media_type == "video" else "image",
            media_source=asset.url,
            caption=caption,
            status=post_status,
            scheduled_time=scheduled_time,
            media_asset_id=asset.id,
            auto_generated=True,
            caption_source="ai",
        )
        db.add(post)

        # Reshare the same media as a Story a few minutes later — staggered so the two
        # container-creation calls don't land on the Graph API in the same instant.
        story_post = Post(
            customer_id=customer.id,
            media_type="story",
            media_source=asset.url,
            caption=caption,
            status=post_status,
            scheduled_time=scheduled_time + timedelta(minutes=5),
            media_asset_id=asset.id,
            auto_generated=True,
            caption_source="ai",
        )
        db.add(story_post)

        asset.status = "used"
        asset.used_at = datetime.now(timezone.utc)

        settings.last_planned_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(post)
        db.refresh(story_post)

        asset.used_in_post_id = post.id
        db.commit()

        if post_status == "pending":
            from .multi_scheduler import get_mt_scheduler

            scheduler = get_mt_scheduler()
            scheduler.schedule_existing_post(post.id, scheduled_time)
            scheduler.schedule_existing_post(story_post.id, story_post.scheduled_time)

        log_activity(
            db, customer.id, "autopilot_post_planned",
            f"Planned {post.media_type} for {scheduled_time.isoformat()} plus a story "
            f"for {story_post.scheduled_time.isoformat()} (status={post_status})",
        )
        # log_activity's own commit re-expires session objects; refresh once more so the
        # returned instance (including its lazy-loaded customer relationship, used by
        # Post.to_dict()) stays readable after this function's session closes below.
        db.refresh(post)
        _ = post.customer
        return post
    finally:
        db.close()


async def run_autopilot_tick() -> None:
    """Iterate every customer with autopilot enabled and plan their next post if due."""
    db = get_db()
    try:
        customer_ids = [
            row.customer_id
            for row in db.query(AutopilotSettings).filter(AutopilotSettings.enabled == True).all()  # noqa: E712
        ]
    finally:
        db.close()

    for customer_id in customer_ids:
        try:
            await plan_next_post(customer_id)
        except Exception as e:
            print(f"[GrowthAgent] autopilot tick failed for customer {customer_id}: {e}", file=sys.stderr)
