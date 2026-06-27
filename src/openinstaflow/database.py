"""
Database layer for the multi-tenant OpenInstaFlow SaaS platform.

Uses SQLAlchemy with SQLite. Instagram access tokens are encrypted at rest
using Fernet (symmetric) encryption from the ``cryptography`` package.
"""

from __future__ import annotations

import os
import secrets
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
    create_engine,
    event,
)
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker


class UTCDateTime(TypeDecorator):
    """DateTime that round-trips as timezone-aware UTC.

    SQLite has no native timezone-aware datetime type, so SQLAlchemy's
    plain DateTime silently drops tzinfo on every read/write. A value
    stored as "UTC 11:34" comes back as a naive "11:34", which callers
    then format/compare as if it were already local time (the cause of
    posts displaying ~5:30 off for IST users). This decorator normalizes
    to UTC before writing and re-attaches UTC tzinfo on read.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

# ──────────────────────────────────────────────────────────────────────────────
# Encryption helpers
# ──────────────────────────────────────────────────────────────────────────────

_fernet: Optional[Fernet] = None


def _get_fernet() -> Fernet:
    """Get or create the Fernet cipher. Key is read from ENCRYPTION_KEY env var."""
    global _fernet
    if _fernet is None:
        key = os.environ.get("ENCRYPTION_KEY", "").strip()
        if not key:
            # Auto-generate and warn
            key = Fernet.generate_key().decode()
            os.environ["ENCRYPTION_KEY"] = key
            import sys

            print(
                f"[OpenInstaFlow] WARNING: ENCRYPTION_KEY not set. Auto-generated: {key}\n"
                f"  Save this in your .env to persist encrypted data across restarts!",
                file=sys.stderr,
            )
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def encrypt_token(plaintext: str) -> str:
    """Encrypt a token for storage."""
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Decrypt a stored token."""
    if not ciphertext:
        return ""
    return _get_fernet().decrypt(ciphertext.encode()).decode()


# ──────────────────────────────────────────────────────────────────────────────
# Database setup
# ──────────────────────────────────────────────────────────────────────────────

Base = declarative_base()

_engine = None
_SessionLocal = None


def _enable_wal(dbapi_conn, connection_record):
    """Enable WAL mode for better concurrent read performance (SQLite only)."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def init_db(database_url: Optional[str] = None) -> None:
    """Initialize the database engine and create tables."""
    global _engine, _SessionLocal

    url = database_url or os.environ.get("DATABASE_URL", "sqlite:///data/openinstaflow.db")

    # A *relative* sqlite path (e.g. "sqlite:///data/openinstaflow.db", three
    # slashes) resolves against the process's current working directory, not
    # this package's location. Starting the server from a different cwd would
    # silently create a brand-new, empty database — wiping out every customer,
    # activation code, and post from the operator's point of view. Anchor it
    # to the project root instead so the on-disk location is always the same
    # regardless of how/where the process is launched. An *absolute* path
    # (four slashes, e.g. "sqlite:////app/data/openinstaflow.db" — the form
    # you should use in production, pointed at a mounted persistent
    # disk/volume) or a non-sqlite URL (e.g. postgres://...) is left alone.
    if url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
        rel_path = url[len("sqlite:///"):]
        project_root = Path(__file__).resolve().parent.parent.parent
        abs_path = (project_root / rel_path).resolve()
        url = f"sqlite:///{abs_path.as_posix()}"

    is_sqlite = url.startswith("sqlite:")

    # Ensure the data directory exists (sqlite only — Postgres/Supabase has no local path)
    if url.startswith("sqlite:///"):
        db_path = url[len("sqlite:///"):]
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)

    print(f"[OpenInstaFlow] Database: {url.split('@')[-1] if '@' in url else url}", file=sys.stderr)

    connect_args = {"check_same_thread": False} if is_sqlite else {}
    _engine = create_engine(url, echo=False, connect_args=connect_args)
    if is_sqlite:
        event.listen(_engine, "connect", _enable_wal)

    _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)

    # Create all tables
    Base.metadata.create_all(bind=_engine)

    # Patch columns onto a table that already existed before the column was added to the
    # model — applies to BOTH SQLite and Postgres/Supabase. create_all() above only creates
    # *missing tables*; it never ALTERs an existing table to add a column the model gained
    # since that table was first created.
    _migrate_schema(is_sqlite)


def _migrate_schema(is_sqlite: bool) -> None:
    """Add columns that were introduced after a table already existed."""
    additions = {
        "posts": [
            ("media_asset_id", "VARCHAR(36)"),
            ("auto_generated", "BOOLEAN DEFAULT 0" if is_sqlite else "BOOLEAN DEFAULT FALSE"),
            ("caption_source", "VARCHAR(20)"),
        ],
    }
    with _engine.connect() as conn:
        if is_sqlite:
            # SQLite's ALTER TABLE has no IF NOT EXISTS, so check PRAGMA table_info first.
            for table, columns in additions.items():
                existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
                for name, ddl_type in columns:
                    if name not in existing:
                        conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}")
        else:
            # Postgres supports IF NOT EXISTS directly, so this is idempotent on every startup.
            for table, columns in additions.items():
                for name, ddl_type in columns:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {ddl_type}")
        conn.commit()


def get_db() -> Session:
    """Get a database session."""
    if _SessionLocal is None:
        init_db()
    return _SessionLocal()


# ──────────────────────────────────────────────────────────────────────────────
# Models
# ──────────────────────────────────────────────────────────────────────────────


class Admin(Base):
    """An admin's profile row. ``id`` is the Supabase Auth user id for this account —
    Supabase owns the password, this table only owns app-specific role/profile data."""

    __tablename__ = "admins"

    id = Column(String(36), primary_key=True)
    email = Column(String(255), unique=True, nullable=False)
    created_at = Column(UTCDateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    activation_codes = relationship("ActivationCode", back_populates="created_by_admin")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Customer(Base):
    """A customer's profile row. ``id`` is the Supabase Auth user id for this account —
    Supabase owns the password, this table only owns app-specific profile/IG data."""

    __tablename__ = "customers"

    id = Column(String(36), primary_key=True)
    email = Column(String(255), unique=True, nullable=False)
    name = Column(String(200), nullable=False)

    # Instagram credentials (token encrypted at rest)
    ig_username = Column(String(100), nullable=True)
    ig_user_id = Column(String(100), nullable=True)
    ig_access_token_enc = Column(Text, nullable=True)  # Fernet-encrypted
    login_kind = Column(String(20), default="ig_login")
    fb_page_id = Column(String(100), nullable=True)
    fb_page_access_token_enc = Column(Text, nullable=True)

    # Status
    status = Column(String(20), default="pending")  # pending, active, paused, expired
    activated_at = Column(UTCDateTime, nullable=True)
    created_at = Column(UTCDateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    posts = relationship("Post", back_populates="customer", cascade="all, delete-orphan")
    activity_logs = relationship("ActivityLog", back_populates="customer", cascade="all, delete-orphan")
    activation_code = relationship("ActivationCode", back_populates="customer", uselist=False)
    media_assets = relationship("MediaAsset", back_populates="customer", cascade="all, delete-orphan")
    autopilot_settings = relationship("AutopilotSettings", back_populates="customer", uselist=False, cascade="all, delete-orphan")
    strategy_insights = relationship("StrategyInsight", back_populates="customer", cascade="all, delete-orphan")

    @property
    def ig_access_token(self) -> str:
        return decrypt_token(self.ig_access_token_enc or "")

    @ig_access_token.setter
    def ig_access_token(self, value: str) -> None:
        self.ig_access_token_enc = encrypt_token(value) if value else ""

    @property
    def fb_page_access_token(self) -> str:
        return decrypt_token(self.fb_page_access_token_enc or "")

    @fb_page_access_token.setter
    def fb_page_access_token(self, value: str) -> None:
        self.fb_page_access_token_enc = encrypt_token(value) if value else ""

    def to_dict(self, include_sensitive: bool = False) -> dict:
        d = {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "ig_username": self.ig_username,
            "ig_user_id": self.ig_user_id,
            "login_kind": self.login_kind,
            "status": self.status,
            "activated_at": self.activated_at.isoformat() if self.activated_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "post_count": len(self.posts) if self.posts else 0,
        }
        if include_sensitive:
            d["ig_access_token"] = self.ig_access_token
            d["fb_page_id"] = self.fb_page_id
        return d


class ActivationCode(Base):
    __tablename__ = "activation_codes"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    code = Column(String(32), unique=True, nullable=False, default=lambda: secrets.token_hex(12))
    customer_id = Column(String(36), ForeignKey("customers.id"), nullable=True)
    created_by_admin_id = Column(String(36), ForeignKey("admins.id"), nullable=True)
    created_at = Column(UTCDateTime, default=lambda: datetime.now(timezone.utc))
    redeemed_at = Column(UTCDateTime, nullable=True)

    # Relationships
    customer = relationship("Customer", back_populates="activation_code")
    created_by_admin = relationship("Admin", back_populates="activation_codes")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "code": self.code,
            "customer_id": self.customer_id,
            "customer_email": self.customer.email if self.customer else None,
            "customer_name": self.customer.name if self.customer else None,
            "is_redeemed": self.customer_id is not None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "redeemed_at": self.redeemed_at.isoformat() if self.redeemed_at else None,
        }


class Post(Base):
    __tablename__ = "posts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_id = Column(String(36), ForeignKey("customers.id"), nullable=False)

    media_type = Column(String(20), nullable=False)  # image, reel, story, carousel
    media_source = Column(Text, nullable=False)  # URL or path
    caption = Column(Text, nullable=True)
    status = Column(String(20), default="pending")  # draft, pending, publishing, published, failed, cancelled
    scheduled_time = Column(UTCDateTime, nullable=True)
    published_at = Column(UTCDateTime, nullable=True)
    ig_media_id = Column(String(100), nullable=True)
    permalink = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(UTCDateTime, default=lambda: datetime.now(timezone.utc))

    # Extra publish options
    share_to_feed = Column(Boolean, nullable=True)
    cover_url = Column(Text, nullable=True)
    is_local = Column(Boolean, default=False)

    # Autopilot / AI metadata
    media_asset_id = Column(String(36), ForeignKey("media_assets.id"), nullable=True)
    auto_generated = Column(Boolean, default=False)
    caption_source = Column(String(20), nullable=True)  # manual, ai

    # Relationships
    customer = relationship("Customer", back_populates="posts")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else None,
            "media_type": self.media_type,
            "media_source": self.media_source,
            "caption": self.caption,
            "status": self.status,
            "scheduled_time": self.scheduled_time.isoformat() if self.scheduled_time else None,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "ig_media_id": self.ig_media_id,
            "permalink": self.permalink,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "share_to_feed": self.share_to_feed,
            "is_local": self.is_local,
            "media_asset_id": self.media_asset_id,
            "auto_generated": self.auto_generated,
            "caption_source": self.caption_source,
        }


class MediaAsset(Base):
    """A piece of media a customer has uploaded for the autopilot queue to consume."""

    __tablename__ = "media_assets"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_id = Column(String(36), ForeignKey("customers.id"), nullable=False)

    media_type = Column(String(20), nullable=False)  # image, video
    file_path = Column(Text, nullable=False)  # R2 object key (see media_store.py), not a local path
    url = Column(Text, nullable=False)
    caption_hint = Column(Text, nullable=True)
    status = Column(String(20), default="queued")  # queued, used, failed
    used_in_post_id = Column(String(36), nullable=True)
    created_at = Column(UTCDateTime, default=lambda: datetime.now(timezone.utc))
    used_at = Column(UTCDateTime, nullable=True)

    customer = relationship("Customer", back_populates="media_assets")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else None,
            "media_type": self.media_type,
            "url": self.url,
            "caption_hint": self.caption_hint,
            "status": self.status,
            "used_in_post_id": self.used_in_post_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "used_at": self.used_at.isoformat() if self.used_at else None,
        }


class AutopilotSettings(Base):
    """Per-customer configuration for the growth agent's autopilot behavior."""

    __tablename__ = "autopilot_settings"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_id = Column(String(36), ForeignKey("customers.id"), unique=True, nullable=False)

    enabled = Column(Boolean, default=False)
    auto_publish = Column(Boolean, default=False)  # False = draft-and-approve, True = fully autonomous
    niche = Column(Text, nullable=True)
    goal = Column(Text, nullable=True)  # e.g. "engagement", "leads", "awareness"
    tone = Column(Text, nullable=True)
    target_location = Column(Text, nullable=True)
    timezone = Column(String(64), default="UTC")
    posts_per_week = Column(Integer, default=3)
    preferred_hours = Column(Text, nullable=True)  # JSON list of ints, e.g. "[9, 13, 19]"
    last_planned_at = Column(UTCDateTime, nullable=True)
    updated_at = Column(UTCDateTime, default=lambda: datetime.now(timezone.utc))

    customer = relationship("Customer", back_populates="autopilot_settings")

    def to_dict(self) -> dict:
        import json as _json

        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "enabled": self.enabled,
            "auto_publish": self.auto_publish,
            "niche": self.niche,
            "goal": self.goal,
            "tone": self.tone,
            "target_location": self.target_location,
            "timezone": self.timezone,
            "posts_per_week": self.posts_per_week,
            "preferred_hours": _json.loads(self.preferred_hours) if self.preferred_hours else None,
            "last_planned_at": self.last_planned_at.isoformat() if self.last_planned_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class StrategyInsight(Base):
    """A snapshot produced by the growth agent's account analysis."""

    __tablename__ = "strategy_insights"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_id = Column(String(36), ForeignKey("customers.id"), nullable=False)

    generated_at = Column(UTCDateTime, default=lambda: datetime.now(timezone.utc))
    best_hours = Column(Text, nullable=True)  # JSON list of ints
    best_days = Column(Text, nullable=True)  # JSON list of weekday ints (0=Mon)
    audience_locations = Column(Text, nullable=True)  # JSON dict/list
    top_themes = Column(Text, nullable=True)  # JSON list
    summary = Column(Text, nullable=True)
    source = Column(String(20), default="manual")  # graph, manual, mixed

    customer = relationship("Customer", back_populates="strategy_insights")

    def to_dict(self) -> dict:
        import json as _json

        def _loads(v):
            return _json.loads(v) if v else None

        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
            "best_hours": _loads(self.best_hours),
            "best_days": _loads(self.best_days),
            "audience_locations": _loads(self.audience_locations),
            "top_themes": _loads(self.top_themes),
            "summary": self.summary,
            "source": self.source,
        }


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_id = Column(String(36), ForeignKey("customers.id"), nullable=True)
    action = Column(String(100), nullable=False)
    details = Column(Text, nullable=True)
    timestamp = Column(UTCDateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    customer = relationship("Customer", back_populates="activity_logs")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else None,
            "action": self.action,
            "details": self.details,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


# ──────────────────────────────────────────────────────────────────────────────
# CRUD Helpers
# ──────────────────────────────────────────────────────────────────────────────


def log_activity(db: Session, customer_id: Optional[str], action: str, details: Optional[str] = None) -> None:
    """Log an activity event."""
    entry = ActivityLog(customer_id=customer_id, action=action, details=details)
    db.add(entry)
    db.commit()
