"""
Pulls new images/videos out of a customer's linked Google Drive folder and drops them into
the autopilot media queue (``MediaAsset``), so ``growth_agent.plan_next_post`` can consume
them exactly like a manually uploaded file.

Files are downloaded server-side and re-uploaded to R2 (via ``media_store.put_bytes``) rather
than published straight from Drive — that keeps the OAuth scope read-only (no need to touch
permissions on files the customer didn't create through this app) and means the publish path
in ``multi_scheduler.py`` needs no changes.
"""

from __future__ import annotations

import asyncio
import io
import sys

from . import gdrive_oauth, media_store
from .database import Customer, MediaAsset, get_db, log_activity

DRIVE_MEDIA_QUERY = (
    "'{folder_id}' in parents and trashed = false and "
    "(mimeType contains 'image/' or mimeType contains 'video/')"
)

DEFAULT_IMPORT_LIMIT = 20


def _build_drive_service(access_token: str):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(token=access_token)
    return build("drive", "v3", credentials=creds)


async def _get_access_token(customer: Customer) -> str:
    refresh_token = customer.google_drive_refresh_token
    if not refresh_token:
        raise RuntimeError("Customer has no linked Google Drive account")
    return await gdrive_oauth.refresh_access_token(refresh_token)


async def list_folders(customer: Customer) -> list[dict]:
    """List the customer's Drive folders, for the folder-picker dropdown."""
    access_token = await _get_access_token(customer)
    loop = asyncio.get_event_loop()

    def _list() -> list[dict]:
        service = _build_drive_service(access_token)
        resp = (
            service.files()
            .list(
                q="mimeType = 'application/vnd.google-apps.folder' and trashed = false",
                fields="files(id,name)",
                pageSize=200,
                orderBy="name",
            )
            .execute()
        )
        return resp.get("files", [])

    return await loop.run_in_executor(None, _list)


def _list_media_files(service, folder_id: str) -> list[dict]:
    resp = (
        service.files()
        .list(
            q=DRIVE_MEDIA_QUERY.format(folder_id=folder_id),
            fields="files(id,name,mimeType,createdTime)",
            orderBy="createdTime",
            pageSize=200,
        )
        .execute()
    )
    return resp.get("files", [])


def _download_file(service, file_id: str) -> bytes:
    from googleapiclient.http import MediaIoBaseDownload

    buf = io.BytesIO()
    request = service.files().get_media(fileId=file_id)
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buf.seek(0)
    return buf.read()


async def sync_customer_drive(customer_id: str, limit: int = DEFAULT_IMPORT_LIMIT) -> int:
    """
    Import any new files from the customer's linked Drive folder into the media queue.

    Returns the number of files imported. Safe to call repeatedly — already-imported files
    (tracked by ``MediaAsset.source_file_id``) are skipped.
    """
    db = get_db()
    try:
        customer = db.query(Customer).filter(Customer.id == customer_id).first()
        if not customer or not customer.google_drive_refresh_token or not customer.google_drive_folder_id:
            return 0

        access_token = await _get_access_token(customer)
        folder_id = customer.google_drive_folder_id

        loop = asyncio.get_event_loop()
        service = await loop.run_in_executor(None, _build_drive_service, access_token)
        files = await loop.run_in_executor(None, _list_media_files, service, folder_id)

        already_imported = {
            row.source_file_id
            for row in db.query(MediaAsset).filter(
                MediaAsset.customer_id == customer_id,
                MediaAsset.source == "google_drive",
            ).all()
            if row.source_file_id
        }

        new_files = [f for f in files if f["id"] not in already_imported]
        to_import = new_files[:limit]

        imported = 0
        for f in to_import:
            try:
                content = await loop.run_in_executor(None, _download_file, service, f["id"])
                object_key, public_url, media_type = await loop.run_in_executor(
                    None, media_store.put_bytes, customer_id, f["name"], content
                )
            except ValueError:
                continue  # unsupported extension despite the mimeType filter; skip it

            asset = MediaAsset(
                customer_id=customer_id,
                media_type=media_type,
                file_path=object_key,
                url=public_url,
                status="queued",
                source="google_drive",
                source_file_id=f["id"],
            )
            db.add(asset)
            imported += 1

        if imported:
            db.commit()

        remaining = len(new_files) - len(to_import)
        details = f"Imported {imported} file(s) from Drive folder '{customer.google_drive_folder_name}'."
        if remaining > 0:
            details += f" {remaining} more pending — will continue on the next sync."
        if imported or remaining:
            log_activity(db, customer_id, "google_drive_synced", details)

        return imported
    except Exception as e:
        print(f"[GDriveSync] sync failed for customer {customer_id}: {e}", file=sys.stderr)
        raise
    finally:
        db.close()
