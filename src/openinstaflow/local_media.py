"""
Local media → Google Drive upload for Instagram publishing.

Instagram's Graph API requires media at a public HTTPS URL — it can't accept file uploads.
This module bridges that gap by:
  1. Uploading the local file to Google Drive via the API
  2. Setting the file to "anyone with the link can view"
  3. Returning a direct download URL for use with the publish_* functions
  4. Cleaning up uploaded files after publishing (optional)

Requires a Google Service Account:
  1. Go to https://console.cloud.google.com → APIs & Services → Credentials
  2. Create a Service Account, download the JSON key file
  3. Enable the Google Drive API for your project
  4. Set GOOGLE_SERVICE_ACCOUNT_JSON in .env to the path of the JSON key file
  5. (Optional) Set GOOGLE_DRIVE_FOLDER_ID to upload into a specific folder
"""

from __future__ import annotations

import mimetypes
import os
import re
from pathlib import Path
from typing import Optional

# ──────────────────────────────────────────────────────────────────────────────
# Google Drive URL conversion
# ──────────────────────────────────────────────────────────────────────────────

# Patterns for Google Drive share links
_GDRIVE_FILE_PATTERN = re.compile(
    r"https?://drive\.google\.com/file/d/([a-zA-Z0-9_-]+)", re.IGNORECASE
)
_GDRIVE_OPEN_PATTERN = re.compile(
    r"https?://drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)", re.IGNORECASE
)
_GDRIVE_UC_PATTERN = re.compile(
    r"https?://drive\.google\.com/uc\?.*id=([a-zA-Z0-9_-]+)", re.IGNORECASE
)


def is_gdrive_url(url: str) -> bool:
    """Check if a URL is a Google Drive link (share page, not direct download)."""
    return bool(
        _GDRIVE_FILE_PATTERN.search(url)
        or _GDRIVE_OPEN_PATTERN.search(url)
        or _GDRIVE_UC_PATTERN.search(url)
    )


def convert_gdrive_url(url: str) -> str:
    """
    Convert a Google Drive share/view URL to a direct download URL.

    Supports:
      - https://drive.google.com/file/d/{ID}/view?usp=sharing
      - https://drive.google.com/open?id={ID}
      - https://drive.google.com/uc?id={ID}&export=download  (already direct, returned as-is)

    Returns the original URL unchanged if it's not a recognized Drive link.
    """
    # Already a direct download link
    if "uc?" in url and "export=download" in url:
        return url

    # /file/d/{ID}/...
    m = _GDRIVE_FILE_PATTERN.search(url)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"

    # /open?id={ID}
    m = _GDRIVE_OPEN_PATTERN.search(url)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"

    # /uc?id={ID} without export=download
    m = _GDRIVE_UC_PATTERN.search(url)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"

    return url

# ──────────────────────────────────────────────────────────────────────────────
# Supported media types
# ──────────────────────────────────────────────────────────────────────────────

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".m4v"}
ALL_MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

MIME_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".wmv": "video/x-ms-wmv",
    ".m4v": "video/x-m4v",
}


def detect_media_type(path: str) -> str:
    """Return 'image' or 'video' based on file extension, or raise."""
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    raise ValueError(
        f"Unsupported file type '{ext}'. Supported: {', '.join(sorted(ALL_MEDIA_EXTENSIONS))}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Google Drive uploader
# ──────────────────────────────────────────────────────────────────────────────


class GoogleDriveUploader:
    """
    Uploads local media files to Google Drive and returns public direct URLs.

    Usage::

        uploader = GoogleDriveUploader(service_account_json="path/to/key.json")
        uploader.authenticate()
        url, file_id = uploader.upload_file("C:/Photos/sunset.jpg")
        # url → "https://drive.google.com/uc?export=download&id=xxxx"
        ...
        uploader.delete_file(file_id)  # clean up
    """

    def __init__(
        self,
        service_account_json: Optional[str] = None,
        folder_id: Optional[str] = None,
    ) -> None:
        self._service_account_json = service_account_json
        self._folder_id = folder_id
        self._service = None
        self._authenticated = False
        # Track uploaded files for cleanup
        self._uploaded_files: dict[str, str] = {}  # url → file_id

    @property
    def is_authenticated(self) -> bool:
        return self._authenticated

    def authenticate(self) -> None:
        """Authenticate with Google Drive using a service account."""
        if self._authenticated:
            return

        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError:
            raise RuntimeError(
                "Google API packages required. Install: pip install google-api-python-client google-auth"
            )

        json_path = self._service_account_json
        if not json_path:
            raise RuntimeError(
                "GOOGLE_SERVICE_ACCOUNT_JSON not set. "
                "Create a Google Service Account, download the JSON key, "
                "and set the path in .env"
            )

        if not os.path.exists(json_path):
            raise FileNotFoundError(f"Service account JSON not found: {json_path}")

        credentials = service_account.Credentials.from_service_account_file(
            json_path,
            scopes=["https://www.googleapis.com/auth/drive.file"],
        )

        self._service = build("drive", "v3", credentials=credentials)
        self._authenticated = True

    def upload_file(self, local_path: str) -> tuple[str, str]:
        """
        Upload a local file to Google Drive and make it publicly accessible.

        Args:
            local_path: Absolute path to a local image or video file.

        Returns:
            Tuple of (direct_download_url, file_id).
        """
        if not self._authenticated:
            raise RuntimeError("Not authenticated. Call authenticate() first.")

        path = Path(local_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"File not found: {local_path}")
        if not path.is_file():
            raise ValueError(f"Not a file: {local_path}")

        # Validate it's a supported media type
        detect_media_type(str(path))

        # Determine MIME type
        ext = path.suffix.lower()
        mime_type = MIME_MAP.get(ext) or mimetypes.guess_type(str(path))[0] or "application/octet-stream"

        try:
            from googleapiclient.http import MediaFileUpload
        except ImportError:
            raise RuntimeError("google-api-python-client required.")

        # File metadata
        file_metadata: dict = {"name": path.name}
        if self._folder_id:
            file_metadata["parents"] = [self._folder_id]

        # Upload the file
        media = MediaFileUpload(str(path), mimetype=mime_type, resumable=True)
        uploaded = (
            self._service.files()
            .create(body=file_metadata, media_body=media, fields="id,name,webViewLink")
            .execute()
        )
        file_id = uploaded["id"]

        # Make the file publicly accessible (anyone with the link can view)
        self._service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()

        # Generate direct download URL
        direct_url = f"https://drive.google.com/uc?export=download&id={file_id}"

        # Track for cleanup
        self._uploaded_files[direct_url] = file_id

        return direct_url, file_id

    def delete_file(self, file_id: str) -> bool:
        """Delete a file from Google Drive (cleanup after publishing)."""
        if not self._authenticated or not self._service:
            return False
        try:
            self._service.files().delete(fileId=file_id).execute()
            # Remove from tracking
            self._uploaded_files = {
                url: fid for url, fid in self._uploaded_files.items() if fid != file_id
            }
            return True
        except Exception:
            return False  # best-effort cleanup

    def cleanup_file(self, url: str) -> bool:
        """Delete a previously uploaded file by its public URL (best-effort)."""
        file_id = self._uploaded_files.get(url)
        if file_id:
            return self.delete_file(file_id)
        return False

    def cleanup_all(self) -> None:
        """Delete all uploaded files from Google Drive."""
        for file_id in list(self._uploaded_files.values()):
            self.delete_file(file_id)


# ──────────────────────────────────────────────────────────────────────────────
# Module-level singleton for shared use
# ──────────────────────────────────────────────────────────────────────────────

_uploader: Optional[GoogleDriveUploader] = None


def get_google_drive_uploader(
    service_account_json: Optional[str] = None,
    folder_id: Optional[str] = None,
) -> GoogleDriveUploader:
    """Get or create the singleton GoogleDriveUploader instance."""
    global _uploader
    if _uploader is None or not _uploader.is_authenticated:
        sa_json = service_account_json or os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip() or None
        fid = folder_id or os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip() or None
        _uploader = GoogleDriveUploader(service_account_json=sa_json, folder_id=fid)
        _uploader.authenticate()
    return _uploader


def shutdown_google_drive_uploader() -> None:
    """Clean up all uploaded files and reset the singleton."""
    global _uploader
    if _uploader:
        _uploader.cleanup_all()
    _uploader = None
