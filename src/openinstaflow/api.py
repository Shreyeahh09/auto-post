"""
FastAPI REST API for the multi-tenant OpenInstaFlow SaaS platform.

Serves both the REST API and the static dashboard files.
Run with: ``python -m openinstaflow.api`` or ``uvicorn openinstaflow.api:app``
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# Load .env before any of our own modules are imported below, so env vars are
# guaranteed to be in os.environ no matter when a module happens to read them.
# On Render/Railway this is a no-op (no .env file is deployed; secrets come in
# as real env vars already set before the process starts).
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import Depends, FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr

from . import ai_caption, growth_agent, local_media, media_store, supabase_auth
from .client import InstagramClient
from .config import IgConfig
from .database import (
    ActivationCode,
    ActivityLog,
    Admin,
    AutopilotSettings,
    Customer,
    MediaAsset,
    Post,
    StrategyInsight,
    get_db,
    init_db,
    log_activity,
)
from .multi_scheduler import get_mt_scheduler, shutdown_mt_scheduler

# ──────────────────────────────────────────────────────────────────────────────
# Lifespan
# ──────────────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    init_db()
    get_mt_scheduler()
    print("[OpenInstaFlow] API server started.", file=sys.stderr)
    yield
    shutdown_mt_scheduler()
    print("[OpenInstaFlow] API server stopped.", file=sys.stderr)


app = FastAPI(
    title="OpenInstaFlow SaaS API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    """Unauthenticated liveness check for load balancers / Docker HEALTHCHECK."""
    return {"status": "ok"}


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    """Always return JSON, even for unexpected bugs — the dashboard does response.json()."""
    print(f"[OpenInstaFlow] Unhandled error on {request.method} {request.url.path}: {exc}", file=sys.stderr)
    return JSONResponse(status_code=500, content={"detail": f"Internal server error: {exc}"})


# ──────────────────────────────────────────────────────────────────────────────
# Auth dependency
# ──────────────────────────────────────────────────────────────────────────────


async def _extract_token(authorization: Optional[str] = Header(None)) -> dict:
    """Validate the Supabase access token from the Authorization header and resolve
    which local account (admin or customer) it belongs to.

    Role isn't trusted from the token itself — Supabase issues the same kind of
    token to every user. We look the Supabase user id up in our own admins/customers
    tables, which is also what catches a token whose account no longer exists
    (e.g. after a database reset) with a clear error instead of an ambiguous one.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization[7:]
    user = await supabase_auth.get_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    db = get_db()
    try:
        user_id = user["id"]
        if db.query(Admin).filter(Admin.id == user_id).first():
            role = "admin"
        elif db.query(Customer).filter(Customer.id == user_id).first():
            role = "customer"
        else:
            raise HTTPException(status_code=401, detail="No account found for this user")
        return {"sub": user_id, "role": role, "email": user.get("email")}
    finally:
        db.close()


def require_admin(payload: dict = Depends(_extract_token)) -> dict:
    """Require admin role."""
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return payload


def require_customer(payload: dict = Depends(_extract_token)) -> dict:
    """Require customer role."""
    if payload.get("role") != "customer":
        raise HTTPException(status_code=403, detail="Customer access required")
    return payload


def require_any(payload: dict = Depends(_extract_token)) -> dict:
    """Require any authenticated user."""
    return payload


# ──────────────────────────────────────────────────────────────────────────────
# Request/Response models
# ──────────────────────────────────────────────────────────────────────────────


class AdminLoginRequest(BaseModel):
    email: str
    password: str


class AdminBootstrapRequest(BaseModel):
    email: EmailStr
    password: str


class CustomerLoginRequest(BaseModel):
    email: str
    password: str


class CustomerSignupRequest(BaseModel):
    email: str
    password: str
    name: str
    activation_code: str


class CustomerUpdateRequest(BaseModel):
    name: Optional[str] = None
    ig_username: Optional[str] = None
    ig_user_id: Optional[str] = None
    ig_access_token: Optional[str] = None
    login_kind: Optional[str] = None
    fb_page_id: Optional[str] = None
    fb_page_access_token: Optional[str] = None
    status: Optional[str] = None


class PublishRequest(BaseModel):
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    caption: Optional[str] = None
    media_type: Optional[str] = None
    share_to_feed: Optional[bool] = None
    cover_url: Optional[str] = None
    auto_caption: bool = False


class ScheduleRequest(BaseModel):
    scheduled_time: str
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    caption: Optional[str] = None
    media_type: Optional[str] = None
    share_to_feed: Optional[bool] = None
    cover_url: Optional[str] = None
    auto_caption: bool = False


class GenerateCodesRequest(BaseModel):
    count: int = 1


class AutopilotRequest(BaseModel):
    enabled: Optional[bool] = None
    auto_publish: Optional[bool] = None
    niche: Optional[str] = None
    goal: Optional[str] = None
    tone: Optional[str] = None
    target_location: Optional[str] = None
    timezone: Optional[str] = None
    posts_per_week: Optional[int] = None
    preferred_hours: Optional[list[int]] = None


class ApprovePostRequest(BaseModel):
    caption: Optional[str] = None
    scheduled_time: Optional[str] = None


class MediaPresignRequest(BaseModel):
    filename: str


class MediaConfirmRequest(BaseModel):
    object_key: str
    caption_hint: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────────
# Auth endpoints
# ──────────────────────────────────────────────────────────────────────────────


@app.post("/api/auth/admin/bootstrap")
async def admin_bootstrap(req: AdminBootstrapRequest):
    """One-time setup: create the first admin account. Only works while zero admins
    exist — once any admin exists, this endpoint refuses, so it can't be used by
    anyone else to grant themselves admin access later."""
    db = get_db()
    try:
        if db.query(Admin).count() > 0:
            raise HTTPException(
                status_code=403, detail="An admin already exists. Ask them to grant you access."
            )
        try:
            user = await supabase_auth.create_confirmed_user(req.email, req.password)
        except supabase_auth.SupabaseAuthError as e:
            raise HTTPException(status_code=400, detail=str(e))

        try:
            admin = Admin(id=user["id"], email=req.email)
            db.add(admin)
            db.commit()
        except Exception:
            db.rollback()
            await supabase_auth.delete_user(user["id"])
            raise

        session = await supabase_auth.login(req.email, req.password)
        return {"access_token": session["access_token"], "token_type": "bearer", "role": "admin", "email": req.email}
    finally:
        db.close()


@app.post("/api/auth/admin/login")
async def admin_login(req: AdminLoginRequest):
    """Admin login → Supabase session token."""
    db = get_db()
    try:
        try:
            session = await supabase_auth.login(req.email, req.password)
        except supabase_auth.SupabaseAuthError:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        admin = db.query(Admin).filter(Admin.id == session["user"]["id"]).first()
        if not admin:
            raise HTTPException(status_code=401, detail="This account is not an admin")
        return {
            "access_token": session["access_token"],
            "token_type": "bearer",
            "role": "admin",
            "email": admin.email,
        }
    finally:
        db.close()


@app.post("/api/auth/customer/login")
async def customer_login(req: CustomerLoginRequest):
    """Customer login → Supabase session token."""
    db = get_db()
    try:
        try:
            session = await supabase_auth.login(req.email, req.password)
        except supabase_auth.SupabaseAuthError:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        customer = db.query(Customer).filter(Customer.id == session["user"]["id"]).first()
        if not customer:
            raise HTTPException(status_code=401, detail="This account is not registered as a customer")
        return {
            "access_token": session["access_token"],
            "token_type": "bearer",
            "role": "customer",
            "customer": customer.to_dict(),
        }
    finally:
        db.close()


@app.post("/api/auth/customer/signup")
async def customer_signup(req: CustomerSignupRequest):
    """Customer sign up with activation code."""
    db = get_db()
    try:
        # Validate activation code
        code = db.query(ActivationCode).filter(ActivationCode.code == req.activation_code).first()
        if not code:
            raise HTTPException(status_code=400, detail="Invalid activation code")
        if code.customer_id is not None:
            raise HTTPException(status_code=400, detail="Activation code already used")

        # Check email uniqueness
        existing = db.query(Customer).filter(Customer.email == req.email).first()
        if existing:
            raise HTTPException(status_code=400, detail="Email already registered")

        try:
            user = await supabase_auth.create_confirmed_user(req.email, req.password)
        except supabase_auth.SupabaseAuthError as e:
            raise HTTPException(status_code=400, detail=str(e))

        try:
            # Create customer, bound to this Supabase user's id
            customer = Customer(
                id=user["id"],
                email=req.email,
                name=req.name,
                status="active",
                activated_at=datetime.now(timezone.utc),
            )
            db.add(customer)
            db.flush()

            # Redeem activation code — bound to this customer, can't be reused
            code.customer_id = customer.id
            code.redeemed_at = datetime.now(timezone.utc)

            db.commit()
            db.refresh(customer)
        except Exception:
            db.rollback()
            await supabase_auth.delete_user(user["id"])
            raise

        log_activity(db, customer.id, "customer_signup", f"Customer signed up: {req.email}")

        session = await supabase_auth.login(req.email, req.password)
        return {
            "access_token": session["access_token"],
            "token_type": "bearer",
            "role": "customer",
            "customer": customer.to_dict(),
        }
    finally:
        db.close()


# ──────────────────────────────────────────────────────────────────────────────
# Admin: Dashboard stats
# ──────────────────────────────────────────────────────────────────────────────


@app.get("/api/dashboard/stats")
async def dashboard_stats(payload: dict = Depends(require_admin)):
    """Get aggregate dashboard statistics."""
    db = get_db()
    try:
        total_customers = db.query(Customer).count()
        active_customers = db.query(Customer).filter(Customer.status == "active").count()
        total_posts = db.query(Post).count()
        published_posts = db.query(Post).filter(Post.status == "published").count()
        failed_posts = db.query(Post).filter(Post.status == "failed").count()
        pending_posts = db.query(Post).filter(Post.status == "pending").count()

        # Posts today
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        posts_today = db.query(Post).filter(Post.published_at >= today_start).count()

        # Unused activation codes
        unused_codes = db.query(ActivationCode).filter(ActivationCode.customer_id.is_(None)).count()
        total_codes = db.query(ActivationCode).count()

        success_rate = round((published_posts / total_posts * 100), 1) if total_posts > 0 else 0.0

        return {
            "total_customers": total_customers,
            "active_customers": active_customers,
            "total_posts": total_posts,
            "published_posts": published_posts,
            "failed_posts": failed_posts,
            "pending_posts": pending_posts,
            "posts_today": posts_today,
            "unused_codes": unused_codes,
            "total_codes": total_codes,
            "success_rate": success_rate,
        }
    finally:
        db.close()


# ──────────────────────────────────────────────────────────────────────────────
# Admin: Customers CRUD
# ──────────────────────────────────────────────────────────────────────────────


@app.get("/api/customers")
async def list_customers(payload: dict = Depends(require_admin)):
    """List all customers."""
    db = get_db()
    try:
        customers = db.query(Customer).order_by(Customer.created_at.desc()).all()
        return {"customers": [c.to_dict() for c in customers]}
    finally:
        db.close()


@app.get("/api/customers/{customer_id}")
async def get_customer(customer_id: str, payload: dict = Depends(require_admin)):
    """Get a customer's details."""
    db = get_db()
    try:
        customer = db.query(Customer).filter(Customer.id == customer_id).first()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        return {"customer": customer.to_dict(include_sensitive=True)}
    finally:
        db.close()


@app.put("/api/customers/{customer_id}")
async def update_customer(customer_id: str, req: CustomerUpdateRequest, payload: dict = Depends(require_admin)):
    """Update a customer (admin)."""
    db = get_db()
    try:
        customer = db.query(Customer).filter(Customer.id == customer_id).first()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        if req.name is not None:
            customer.name = req.name
        if req.ig_username is not None:
            customer.ig_username = req.ig_username
        if req.ig_user_id is not None:
            customer.ig_user_id = req.ig_user_id
        if req.ig_access_token is not None:
            customer.ig_access_token = req.ig_access_token
        if req.login_kind is not None:
            customer.login_kind = req.login_kind
        if req.fb_page_id is not None:
            customer.fb_page_id = req.fb_page_id
        if req.fb_page_access_token is not None:
            customer.fb_page_access_token = req.fb_page_access_token
        if req.status is not None:
            customer.status = req.status

        db.commit()
        db.refresh(customer)
        log_activity(db, customer_id, "customer_updated", "Customer profile updated by admin")
        return {"customer": customer.to_dict()}
    finally:
        db.close()


@app.delete("/api/customers/{customer_id}")
async def delete_customer(customer_id: str, payload: dict = Depends(require_admin)):
    """Delete a customer and all their data."""
    db = get_db()
    try:
        customer = db.query(Customer).filter(Customer.id == customer_id).first()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        db.delete(customer)
        db.commit()
        return {"status": "deleted", "customer_id": customer_id}
    finally:
        db.close()


@app.post("/api/customers/{customer_id}/test")
async def test_customer_token(customer_id: str, payload: dict = Depends(require_admin)):
    """Verify a customer's Instagram token works."""
    db = get_db()
    try:
        customer = db.query(Customer).filter(Customer.id == customer_id).first()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        token = customer.ig_access_token
        user_id = customer.ig_user_id

        if not token or not user_id:
            return {"status": "error", "message": "No Instagram credentials configured"}

        cfg = IgConfig(access_token=token, ig_user_id=user_id, login_kind=customer.login_kind or "ig_login")
        client = InstagramClient(cfg)

        try:
            data = await client.get(user_id, {"fields": "username,name,profile_picture_url,followers_count"}, {"token": token})
            # Update cached username
            if isinstance(data, dict) and data.get("username"):
                customer.ig_username = data["username"]
                db.commit()
            return {"status": "ok", "profile": data}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    finally:
        db.close()


# ──────────────────────────────────────────────────────────────────────────────
# Admin: Activation codes
# ──────────────────────────────────────────────────────────────────────────────


@app.get("/api/activation-codes")
async def list_activation_codes(payload: dict = Depends(require_admin)):
    """List all activation codes."""
    db = get_db()
    try:
        codes = db.query(ActivationCode).order_by(ActivationCode.created_at.desc()).all()
        return {"codes": [c.to_dict() for c in codes]}
    finally:
        db.close()


@app.post("/api/activation-codes")
async def generate_activation_codes(req: GenerateCodesRequest, payload: dict = Depends(require_admin)):
    """Generate new activation codes."""
    db = get_db()
    try:
        codes = []
        for _ in range(min(req.count, 50)):  # cap at 50
            code = ActivationCode(created_by_admin_id=payload["sub"])
            db.add(code)
            codes.append(code)
        db.commit()
        for c in codes:
            db.refresh(c)
        return {"codes": [c.to_dict() for c in codes]}
    finally:
        db.close()


@app.delete("/api/activation-codes/{code_id}")
async def delete_activation_code(code_id: str, payload: dict = Depends(require_admin)):
    """Delete an unused activation code."""
    db = get_db()
    try:
        code = db.query(ActivationCode).filter(ActivationCode.id == code_id).first()
        if not code:
            raise HTTPException(status_code=404, detail="Code not found")
        if code.customer_id:
            raise HTTPException(status_code=400, detail="Cannot delete a redeemed code")
        db.delete(code)
        db.commit()
        return {"status": "deleted"}
    finally:
        db.close()


# ──────────────────────────────────────────────────────────────────────────────
# Admin: Activity log
# ──────────────────────────────────────────────────────────────────────────────


@app.get("/api/activity")
async def list_activity(limit: int = 50, payload: dict = Depends(require_admin)):
    """Get recent activity log."""
    db = get_db()
    try:
        entries = (
            db.query(ActivityLog)
            .order_by(ActivityLog.timestamp.desc())
            .limit(min(limit, 200))
            .all()
        )
        return {"activity": [e.to_dict() for e in entries]}
    finally:
        db.close()


# ──────────────────────────────────────────────────────────────────────────────
# Admin: Posts management
# ──────────────────────────────────────────────────────────────────────────────


@app.get("/api/posts")
async def list_all_posts(status: Optional[str] = None, limit: int = 50, payload: dict = Depends(require_admin)):
    """List all posts across all customers."""
    db = get_db()
    try:
        q = db.query(Post).order_by(Post.created_at.desc())
        if status:
            q = q.filter(Post.status == status)
        posts = q.limit(min(limit, 200)).all()
        return {"posts": [p.to_dict() for p in posts]}
    finally:
        db.close()


@app.get("/api/customers/{customer_id}/posts")
async def list_customer_posts(customer_id: str, payload: dict = Depends(require_admin)):
    """List posts for a specific customer."""
    db = get_db()
    try:
        posts = db.query(Post).filter(Post.customer_id == customer_id).order_by(Post.created_at.desc()).all()
        return {"posts": [p.to_dict() for p in posts]}
    finally:
        db.close()


@app.post("/api/customers/{customer_id}/publish")
async def publish_for_customer(customer_id: str, req: PublishRequest, payload: dict = Depends(require_admin)):
    """Publish a post immediately for a customer (admin action)."""
    db = get_db()
    try:
        customer = db.query(Customer).filter(Customer.id == customer_id).first()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        if customer.status != "active":
            raise HTTPException(status_code=400, detail="Customer account is not active")
        if not customer.ig_access_token or not customer.ig_user_id:
            raise HTTPException(status_code=400, detail="Customer has no Instagram credentials")

        return await _do_publish(db, customer, req)
    finally:
        db.close()


@app.post("/api/customers/{customer_id}/schedule")
async def schedule_for_customer(customer_id: str, req: ScheduleRequest, payload: dict = Depends(require_admin)):
    """Schedule a post for a customer (admin action)."""
    db = get_db()
    try:
        customer = db.query(Customer).filter(Customer.id == customer_id).first()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        return await _do_schedule(customer, req)
    finally:
        db.close()


@app.delete("/api/posts/{post_id}")
async def cancel_post(post_id: str, payload: dict = Depends(require_admin)):
    """Cancel a scheduled post."""
    scheduler = get_mt_scheduler()
    if scheduler.cancel_post(post_id):
        return {"status": "cancelled", "post_id": post_id}
    raise HTTPException(status_code=400, detail="Cannot cancel post (not found or not pending)")


# ──────────────────────────────────────────────────────────────────────────────
# Admin: Media queue (cross-customer)
# ──────────────────────────────────────────────────────────────────────────────


@app.get("/api/media")
async def list_all_media(status: Optional[str] = None, limit: int = 100, payload: dict = Depends(require_admin)):
    """List media-queue assets across all customers."""
    db = get_db()
    try:
        q = db.query(MediaAsset).order_by(MediaAsset.created_at.desc())
        if status:
            q = q.filter(MediaAsset.status == status)
        assets = q.limit(min(limit, 500)).all()
        return {"assets": [a.to_dict() for a in assets]}
    finally:
        db.close()


@app.get("/api/customers/{customer_id}/media")
async def list_customer_media(customer_id: str, payload: dict = Depends(require_admin)):
    """List media-queue assets for a specific customer."""
    db = get_db()
    try:
        assets = (
            db.query(MediaAsset)
            .filter(MediaAsset.customer_id == customer_id)
            .order_by(MediaAsset.created_at.desc())
            .all()
        )
        return {"assets": [a.to_dict() for a in assets]}
    finally:
        db.close()


@app.delete("/api/media/{asset_id}")
async def delete_media_admin(asset_id: str, payload: dict = Depends(require_admin)):
    """Delete a media-queue asset (admin action, any customer)."""
    db = get_db()
    try:
        asset = db.query(MediaAsset).filter(MediaAsset.id == asset_id).first()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        if asset.status == "used":
            raise HTTPException(status_code=400, detail="Cannot delete an asset that's already been used in a post")
        media_store.delete_object(asset.file_path)
        db.delete(asset)
        db.commit()
        return {"status": "deleted", "asset_id": asset_id}
    finally:
        db.close()


# ──────────────────────────────────────────────────────────────────────────────
# Customer: Self-serve endpoints
# ──────────────────────────────────────────────────────────────────────────────


@app.get("/api/me")
async def get_my_profile(payload: dict = Depends(require_customer)):
    """Get the logged-in customer's profile."""
    db = get_db()
    try:
        customer = db.query(Customer).filter(Customer.id == payload["sub"]).first()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        return {"customer": customer.to_dict()}
    finally:
        db.close()


@app.put("/api/me")
async def update_my_profile(req: CustomerUpdateRequest, payload: dict = Depends(require_customer)):
    """Update the logged-in customer's Instagram credentials."""
    db = get_db()
    try:
        customer = db.query(Customer).filter(Customer.id == payload["sub"]).first()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        # Customers can only update their own IG credentials, not status
        if req.ig_username is not None:
            customer.ig_username = req.ig_username
        if req.ig_user_id is not None:
            customer.ig_user_id = req.ig_user_id
        if req.ig_access_token is not None:
            customer.ig_access_token = req.ig_access_token
        if req.login_kind is not None:
            customer.login_kind = req.login_kind
        if req.name is not None:
            customer.name = req.name

        db.commit()
        db.refresh(customer)
        log_activity(db, customer.id, "profile_updated", "Customer updated their profile")
        return {"customer": customer.to_dict()}
    finally:
        db.close()


@app.post("/api/me/test-token")
async def test_my_token(payload: dict = Depends(require_customer)):
    """Verify the customer's own Instagram token."""
    db = get_db()
    try:
        customer = db.query(Customer).filter(Customer.id == payload["sub"]).first()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        token = customer.ig_access_token
        user_id = customer.ig_user_id

        if not token or not user_id:
            return {"status": "error", "message": "No Instagram credentials configured. Go to Settings to add them."}

        cfg = IgConfig(access_token=token, ig_user_id=user_id, login_kind=customer.login_kind or "ig_login")
        client = InstagramClient(cfg)

        try:
            data = await client.get(user_id, {"fields": "username,name,profile_picture_url,followers_count,media_count"}, {"token": token})
            if isinstance(data, dict) and data.get("username"):
                customer.ig_username = data["username"]
                db.commit()
            return {"status": "ok", "profile": data}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    finally:
        db.close()


@app.get("/api/me/posts")
async def my_posts(limit: int = 50, status: Optional[str] = None, payload: dict = Depends(require_customer)):
    """Get the customer's own post history. Pass status=draft to list AI-planned drafts awaiting approval."""
    db = get_db()
    try:
        q = db.query(Post).filter(Post.customer_id == payload["sub"])
        if status:
            q = q.filter(Post.status == status)
        posts = q.order_by(Post.created_at.desc()).limit(min(limit, 200)).all()
        return {"posts": [p.to_dict() for p in posts]}
    finally:
        db.close()


@app.post("/api/me/posts/{post_id}/approve")
async def approve_draft_post(post_id: str, req: ApprovePostRequest, payload: dict = Depends(require_customer)):
    """Approve an autopilot-generated draft, optionally editing its caption/time, and schedule it."""
    db = get_db()
    try:
        post = db.query(Post).filter(Post.id == post_id, Post.customer_id == payload["sub"]).first()
        if not post:
            raise HTTPException(status_code=404, detail="Post not found")
        if post.status != "draft":
            raise HTTPException(status_code=400, detail=f"Post is not a draft (status='{post.status}')")

        if req.caption is not None:
            post.caption = req.caption
            post.caption_source = "manual"
        if req.scheduled_time:
            try:
                post.scheduled_time = datetime.fromisoformat(req.scheduled_time)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid datetime: '{req.scheduled_time}'")
        if not post.scheduled_time:
            raise HTTPException(status_code=400, detail="Post has no scheduled_time to approve into")

        post.status = "pending"
        db.commit()
        db.refresh(post)

        get_mt_scheduler().schedule_existing_post(post.id, post.scheduled_time)
        log_activity(db, post.customer_id, "draft_approved", f"Draft {post.id} approved for {post.scheduled_time.isoformat()}")

        return {"status": "approved", "post": post.to_dict()}
    finally:
        db.close()


@app.post("/api/me/posts/{post_id}/regenerate-caption")
async def regenerate_draft_caption(post_id: str, payload: dict = Depends(require_customer)):
    """Re-run AI captioning for a draft post the customer isn't happy with."""
    db = get_db()
    try:
        post = db.query(Post).filter(Post.id == post_id, Post.customer_id == payload["sub"]).first()
        if not post:
            raise HTTPException(status_code=404, detail="Post not found")
        if post.status != "draft":
            raise HTTPException(status_code=400, detail=f"Post is not a draft (status='{post.status}')")

        customer = db.query(Customer).filter(Customer.id == payload["sub"]).first()
        settings = db.query(AutopilotSettings).filter_by(customer_id=payload["sub"]).first()
        asset = db.query(MediaAsset).filter(MediaAsset.id == post.media_asset_id).first() if post.media_asset_id else None

        try:
            caption = await ai_caption.generate_caption(
                image_url=post.media_source if post.media_type == "image" else None,
                niche=settings.niche if settings else None,
                tone=settings.tone if settings else None,
                goal=settings.goal if settings else None,
                location=settings.target_location if settings else None,
                caption_hint=asset.caption_hint if asset else None,
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Caption generation failed: {e}")

        post.caption = caption
        post.caption_source = "ai"
        db.commit()
        db.refresh(post)
        return {"post": post.to_dict()}
    finally:
        db.close()


# ──────────────────────────────────────────────────────────────────────────────
# Customer: Media queue
# ──────────────────────────────────────────────────────────────────────────────


@app.post("/api/me/media/presign")
async def presign_media_upload(req: MediaPresignRequest, payload: dict = Depends(require_customer)):
    """Get a presigned R2 URL the browser can PUT a file to directly (no bytes through this server)."""
    try:
        presigned = media_store.create_presigned_upload(payload["sub"], req.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return presigned


@app.post("/api/me/media/confirm")
async def confirm_media_upload(req: MediaConfirmRequest, payload: dict = Depends(require_customer)):
    """Record a media-queue entry after the browser has finished a presigned R2 upload."""
    customer_id = payload["sub"]

    # The object key is namespaced by customer id, so a customer can't confirm someone else's upload.
    if not req.object_key.startswith(f"{customer_id}/"):
        raise HTTPException(status_code=403, detail="Object key does not belong to this account")

    if not media_store.object_exists(req.object_key):
        raise HTTPException(status_code=400, detail="Upload not found in storage — it may have failed or expired")

    try:
        media_type = local_media.detect_media_type(req.object_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    db = get_db()
    try:
        asset = MediaAsset(
            customer_id=customer_id,
            media_type=media_type,
            file_path=req.object_key,
            url=media_store.object_url(req.object_key),
            caption_hint=req.caption_hint,
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        log_activity(db, customer_id, "media_uploaded", f"Uploaded {media_type} via direct browser upload")
        return {"asset": asset.to_dict()}
    finally:
        db.close()


@app.get("/api/me/media")
async def list_media(status: Optional[str] = None, payload: dict = Depends(require_customer)):
    """List the customer's media queue."""
    db = get_db()
    try:
        q = db.query(MediaAsset).filter(MediaAsset.customer_id == payload["sub"])
        if status:
            q = q.filter(MediaAsset.status == status)
        assets = q.order_by(MediaAsset.created_at.desc()).all()
        return {"assets": [a.to_dict() for a in assets]}
    finally:
        db.close()


@app.delete("/api/me/media/{asset_id}")
async def delete_media(asset_id: str, payload: dict = Depends(require_customer)):
    """Delete an unused asset from the media queue."""
    db = get_db()
    try:
        asset = db.query(MediaAsset).filter(MediaAsset.id == asset_id, MediaAsset.customer_id == payload["sub"]).first()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        if asset.status == "used":
            raise HTTPException(status_code=400, detail="Cannot delete an asset that's already been used in a post")
        media_store.delete_object(asset.file_path)
        db.delete(asset)
        db.commit()
        return {"status": "deleted", "asset_id": asset_id}
    finally:
        db.close()


# ──────────────────────────────────────────────────────────────────────────────
# Customer: Autopilot settings & growth agent
# ──────────────────────────────────────────────────────────────────────────────


@app.get("/api/me/autopilot")
async def get_autopilot_settings(payload: dict = Depends(require_customer)):
    """Get the customer's autopilot configuration (creates defaults if none exist)."""
    db = get_db()
    try:
        settings = db.query(AutopilotSettings).filter_by(customer_id=payload["sub"]).first()
        if not settings:
            settings = AutopilotSettings(customer_id=payload["sub"])
            db.add(settings)
            db.commit()
            db.refresh(settings)
        return {"autopilot": settings.to_dict()}
    finally:
        db.close()


@app.put("/api/me/autopilot")
async def update_autopilot_settings(req: AutopilotRequest, payload: dict = Depends(require_customer)):
    """Update the customer's autopilot configuration."""
    db = get_db()
    try:
        settings = db.query(AutopilotSettings).filter_by(customer_id=payload["sub"]).first()
        if not settings:
            settings = AutopilotSettings(customer_id=payload["sub"])
            db.add(settings)

        if req.enabled is not None:
            settings.enabled = req.enabled
        if req.auto_publish is not None:
            settings.auto_publish = req.auto_publish
        if req.niche is not None:
            settings.niche = req.niche
        if req.goal is not None:
            settings.goal = req.goal
        if req.tone is not None:
            settings.tone = req.tone
        if req.target_location is not None:
            settings.target_location = req.target_location
        if req.timezone is not None:
            settings.timezone = req.timezone
        if req.posts_per_week is not None:
            settings.posts_per_week = max(1, req.posts_per_week)
        if req.preferred_hours is not None:
            settings.preferred_hours = json.dumps(req.preferred_hours)
        settings.updated_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(settings)
        log_activity(db, payload["sub"], "autopilot_updated", "Autopilot settings updated")
        return {"autopilot": settings.to_dict()}
    finally:
        db.close()


@app.post("/api/me/autopilot/run-now")
async def run_autopilot_now(payload: dict = Depends(require_customer)):
    """Ask the growth agent to plan the next post immediately (ignores the usual cadence wait)."""
    db = get_db()
    try:
        customer = db.query(Customer).filter(Customer.id == payload["sub"]).first()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        settings = db.query(AutopilotSettings).filter_by(customer_id=payload["sub"]).first()
        if not settings or not settings.enabled:
            raise HTTPException(status_code=400, detail="Autopilot is not enabled. Configure it via PUT /api/me/autopilot first.")
        # Force the cadence check to pass for this manual trigger.
        settings.last_planned_at = None
        db.commit()
    finally:
        db.close()

    post = await growth_agent.plan_next_post(payload["sub"])
    if not post:
        return {"status": "no_post_planned", "message": "No queued media available, or autopilot conditions weren't met."}
    return {"status": "planned", "post": post.to_dict()}


@app.get("/api/me/strategy")
async def get_strategy(payload: dict = Depends(require_customer)):
    """Get the latest growth-agent strategy insight."""
    db = get_db()
    try:
        insight = (
            db.query(StrategyInsight)
            .filter_by(customer_id=payload["sub"])
            .order_by(StrategyInsight.generated_at.desc())
            .first()
        )
        if not insight:
            return {"insight": None, "message": "No strategy analysis yet. Call POST /api/me/strategy/refresh."}
        return {"insight": insight.to_dict()}
    finally:
        db.close()


@app.post("/api/me/strategy/refresh")
async def refresh_strategy(payload: dict = Depends(require_customer)):
    """Force a fresh account analysis (rate-limited to once per hour)."""
    db = get_db()
    try:
        customer = db.query(Customer).filter(Customer.id == payload["sub"]).first()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        latest = (
            db.query(StrategyInsight)
            .filter_by(customer_id=payload["sub"])
            .order_by(StrategyInsight.generated_at.desc())
            .first()
        )
        if latest:
            age = datetime.now(timezone.utc) - latest.generated_at.replace(tzinfo=timezone.utc)
            if age < timedelta(hours=1):
                return {"insight": latest.to_dict(), "message": "Using cached analysis (refreshed within the last hour)."}
    finally:
        db.close()

    insight = await growth_agent.analyze_account(payload["sub"])
    return {"insight": insight.to_dict()}


@app.post("/api/me/publish")
async def my_publish(req: PublishRequest, payload: dict = Depends(require_customer)):
    """Publish a post now (customer self-serve)."""
    db = get_db()
    try:
        customer = db.query(Customer).filter(Customer.id == payload["sub"]).first()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        if customer.status != "active":
            raise HTTPException(status_code=403, detail="Account is not active")
        if not customer.ig_access_token or not customer.ig_user_id:
            raise HTTPException(status_code=400, detail="No Instagram credentials configured")

        return await _do_publish(db, customer, req)
    finally:
        db.close()


@app.post("/api/me/schedule")
async def my_schedule(req: ScheduleRequest, payload: dict = Depends(require_customer)):
    """Schedule a post (customer self-serve)."""
    db = get_db()
    try:
        customer = db.query(Customer).filter(Customer.id == payload["sub"]).first()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        if customer.status != "active":
            raise HTTPException(status_code=403, detail="Account is not active")

        return await _do_schedule(customer, req)
    finally:
        db.close()


@app.delete("/api/me/posts/{post_id}")
async def my_cancel_post(post_id: str, payload: dict = Depends(require_customer)):
    """Cancel own scheduled post."""
    db = get_db()
    try:
        post = db.query(Post).filter(Post.id == post_id, Post.customer_id == payload["sub"]).first()
        if not post:
            raise HTTPException(status_code=404, detail="Post not found")
        if post.status not in ("pending", "draft"):
            raise HTTPException(status_code=400, detail=f"Cannot cancel post with status '{post.status}'")

        if post.status == "draft":
            post.status = "cancelled"
            db.commit()
            log_activity(db, post.customer_id, "post_cancelled", f"Draft {post.id} rejected")
        else:
            scheduler = get_mt_scheduler()
            scheduler.cancel_post(post_id)
        return {"status": "cancelled", "post_id": post_id}
    finally:
        db.close()


@app.get("/api/me/activity")
async def my_activity(limit: int = 30, payload: dict = Depends(require_customer)):
    """Get the customer's own activity log."""
    db = get_db()
    try:
        entries = (
            db.query(ActivityLog)
            .filter(ActivityLog.customer_id == payload["sub"])
            .order_by(ActivityLog.timestamp.desc())
            .limit(min(limit, 100))
            .all()
        )
        return {"activity": [e.to_dict() for e in entries]}
    finally:
        db.close()


# ──────────────────────────────────────────────────────────────────────────────
# Shared publish/schedule helpers
# ──────────────────────────────────────────────────────────────────────────────


async def _resolve_caption(
    customer: Customer,
    *,
    image_url: Optional[str],
    cover_url: Optional[str],
    manual_caption: Optional[str],
    auto_caption: bool,
) -> tuple[Optional[str], str]:
    """Return (caption, source). Generates via OpenAI when requested and none was provided."""
    if manual_caption or not auto_caption:
        return manual_caption, "manual"

    db = get_db()
    try:
        settings = db.query(AutopilotSettings).filter_by(customer_id=customer.id).first()
    finally:
        db.close()

    try:
        caption = await ai_caption.generate_caption(
            image_url=image_url or cover_url,
            niche=settings.niche if settings else None,
            tone=settings.tone if settings else None,
            goal=settings.goal if settings else None,
            location=settings.target_location if settings else None,
        )
        return caption, "ai"
    except Exception:
        return manual_caption, "manual"


async def _do_publish(db, customer: Customer, req: PublishRequest) -> dict:
    """Execute a publish for a customer."""
    from .local_media import convert_gdrive_url
    from .publish import PublishCtx, publish_image, publish_reel, publish_story

    token = customer.ig_access_token
    user_id = customer.ig_user_id
    cfg = IgConfig(access_token=token, ig_user_id=user_id, login_kind=customer.login_kind or "ig_login")
    client = InstagramClient(cfg)
    ctx = PublishCtx(client=client, user_id=user_id, token=token, kind=customer.login_kind or "ig_login")

    image_url = convert_gdrive_url(req.image_url) if req.image_url else None
    video_url = convert_gdrive_url(req.video_url) if req.video_url else None

    caption, caption_source = await _resolve_caption(
        customer,
        image_url=image_url,
        cover_url=req.cover_url,
        manual_caption=req.caption,
        auto_caption=req.auto_caption,
    )

    # Determine media type and source
    if req.media_type == "story":
        outcome = await publish_story(ctx, image_url=image_url, video_url=video_url)
        media_source = video_url or image_url
        eff_type = "story"
    elif req.media_type == "reel" or (video_url and req.media_type != "image"):
        if not video_url:
            raise HTTPException(status_code=400, detail="video_url required for reel")
        outcome = await publish_reel(ctx, video_url, caption, share_to_feed=req.share_to_feed, cover_url=req.cover_url)
        media_source = video_url
        eff_type = "reel"
    elif image_url:
        outcome = await publish_image(ctx, image_url, caption)
        media_source = image_url
        eff_type = "image"
    else:
        raise HTTPException(status_code=400, detail="Provide image_url or video_url")

    # Save to DB
    post = Post(
        customer_id=customer.id,
        media_type=eff_type,
        media_source=media_source,
        caption=caption,
        caption_source=caption_source,
        status="published",
        published_at=datetime.now(timezone.utc),
        ig_media_id=outcome.media_id,
        permalink=outcome.permalink,
    )
    db.add(post)
    db.commit()

    log_activity(db, customer.id, "post_published", f"Published {eff_type}: {outcome.permalink or outcome.media_id}")

    return {"status": "published", "post": post.to_dict()}


async def _do_schedule(customer: Customer, req: ScheduleRequest) -> dict:
    """Schedule a post for a customer."""
    from datetime import datetime as dt

    from .local_media import convert_gdrive_url

    try:
        publish_time = dt.fromisoformat(req.scheduled_time)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid datetime: '{req.scheduled_time}'. Use ISO 8601.")

    image_url = convert_gdrive_url(req.image_url) if req.image_url else None
    video_url = convert_gdrive_url(req.video_url) if req.video_url else None
    cover_url = convert_gdrive_url(req.cover_url) if req.cover_url else None

    # Determine media info
    if video_url:
        media_source = video_url
        media_type = req.media_type or "reel"
    elif image_url:
        media_source = image_url
        media_type = req.media_type or "image"
    else:
        raise HTTPException(status_code=400, detail="Provide image_url or video_url")

    caption, caption_source = await _resolve_caption(
        customer,
        image_url=image_url if media_type == "image" else None,
        cover_url=cover_url,
        manual_caption=req.caption,
        auto_caption=req.auto_caption,
    )

    scheduler = get_mt_scheduler()
    try:
        post = scheduler.schedule_post(
            customer_id=customer.id,
            media_type=media_type,
            media_source=media_source,
            scheduled_time=publish_time,
            caption=caption,
            share_to_feed=req.share_to_feed,
            cover_url=cover_url,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if caption_source == "ai":
        db = get_db()
        try:
            db.query(Post).filter(Post.id == post.id).update({"caption_source": "ai"})
            db.commit()
        finally:
            db.close()

    return {"status": "scheduled", "post": post.to_dict()}


# ──────────────────────────────────────────────────────────────────────────────
# Static file serving (dashboard)
# ──────────────────────────────────────────────────────────────────────────────

DASHBOARD_DIR = Path(__file__).resolve().parent.parent.parent / "dashboard"


@app.get("/")
async def serve_dashboard():
    """Serve the dashboard SPA."""
    index = DASHBOARD_DIR / "index.html"
    if index.exists():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"message": "Dashboard not found. Place dashboard files in the /dashboard directory."})


class NoCacheStaticFiles(StaticFiles):
    """Static files with no-cache instead of the default heuristic caching.

    Without this, browsers can serve js/css straight from cache after a deploy without ever
    asking the server, so a stale ``api.js``/``app.js`` silently lingers (observed in
    production — one file got revalidated on the next load, the other didn't, purely by
    chance of each one's last fetch time). ``no-cache`` still lets the browser cache the
    bytes, it just forces a conditional GET (cheap 304 if unchanged) on every load instead
    of trusting a guessed freshness window.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


# Mount static asset directories (don't mount / to avoid overriding API routes)
if DASHBOARD_DIR.exists():
    css_dir = DASHBOARD_DIR / "css"
    js_dir = DASHBOARD_DIR / "js"
    if css_dir.exists():
        app.mount("/css", NoCacheStaticFiles(directory=str(css_dir)), name="css")
    if js_dir.exists():
        app.mount("/js", NoCacheStaticFiles(directory=str(js_dir)), name="js")


# ──────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────────────────────────────────────


def main():
    """Run the API server."""
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run("openinstaflow.api:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
