"""
Cloudflare R2 (S3-compatible) storage for the customer media queue.

Customer uploads go straight from the browser to R2 via a presigned PUT URL — file bytes
never pass through this server (see ``create_presigned_upload`` + ``/api/me/media/presign``
and ``/api/me/media/confirm`` in ``api.py``). Google Drive imports go through ``put_bytes``
instead, since ``gdrive_sync.py`` already has the bytes in hand after downloading them
server-side from the Drive API.

Requires an R2 bucket with public read access (either the bucket's `r2.dev` subdomain or a
custom domain mapped to it — see R2_PUBLIC_BASE_URL) and CORS configured to allow PUT from
your dashboard's origin, since the browser uploads directly to R2.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Optional

import boto3
from botocore.config import Config

from .local_media import ALL_MEDIA_EXTENSIONS, IMAGE_EXTENSIONS, MIME_MAP

_client = None

PRESIGN_EXPIRY_SECONDS = 600  # 10 minutes — long enough for a slow upload, short enough to limit exposure


def _r2_client():
    global _client
    if _client is None:
        account_id = os.environ["R2_ACCOUNT_ID"]
        _client = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )
    return _client


def _bucket() -> str:
    return os.environ["R2_BUCKET"]


def _public_base_url() -> str:
    return os.environ["R2_PUBLIC_BASE_URL"].rstrip("/")


def object_url(object_key: str) -> str:
    return f"{_public_base_url()}/{object_key}"


def _validate_extension(filename: str) -> tuple[str, str]:
    """Return (ext, media_type), raising ValueError if unsupported."""
    ext = Path(filename).suffix.lower()
    if ext not in ALL_MEDIA_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type '{ext}'. Supported: {', '.join(sorted(ALL_MEDIA_EXTENSIONS))}"
        )
    media_type = "image" if ext in IMAGE_EXTENSIONS else "video"
    return ext, media_type


def create_presigned_upload(customer_id: str, filename: str) -> dict:
    """
    Build a presigned PUT URL the browser can upload a file to directly.

    Returns {object_key, upload_url, public_url, media_type}. Raises ValueError if the
    filename's extension isn't a supported image/video type.
    """
    ext, media_type = _validate_extension(filename)
    object_key = f"{customer_id}/{uuid.uuid4().hex}{ext}"

    upload_url = _r2_client().generate_presigned_url(
        "put_object",
        Params={"Bucket": _bucket(), "Key": object_key},
        ExpiresIn=PRESIGN_EXPIRY_SECONDS,
    )
    return {
        "object_key": object_key,
        "upload_url": upload_url,
        "public_url": object_url(object_key),
        "media_type": media_type,
    }


def object_exists(object_key: str) -> bool:
    """Check that a presigned upload actually landed in R2 before trusting it."""
    try:
        _r2_client().head_object(Bucket=_bucket(), Key=object_key)
        return True
    except Exception:
        return False


def put_bytes(customer_id: str, filename: str, content: bytes) -> tuple[str, str, str]:
    """
    Server-side upload — used by gdrive_sync.py, which already holds the file's bytes.

    Returns (object_key, public_url, media_type).
    """
    ext, media_type = _validate_extension(filename)
    object_key = f"{customer_id}/{uuid.uuid4().hex}{ext}"
    content_type = MIME_MAP.get(ext, "application/octet-stream")

    _r2_client().put_object(Bucket=_bucket(), Key=object_key, Body=content, ContentType=content_type)
    return object_key, object_url(object_key), media_type


def delete_object(object_key: Optional[str]) -> bool:
    """Best-effort delete of a stored object."""
    if not object_key:
        return False
    try:
        _r2_client().delete_object(Bucket=_bucket(), Key=object_key)
        return True
    except Exception:
        return False
