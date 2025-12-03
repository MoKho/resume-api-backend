"""
Google Drive OAuth and file operations service (lazy-imported deps).

Notes:
- Uses Supabase to persist per-user OAuth credentials in table `google_drive_tokens`.
- Performs OAuth web server flow using google_auth_oauthlib.flow.Flow.
- Scopes limited to drive.file (files created/selected by the app/user).
- Adds support for a server-owned Google Drive using a service account for
    storing permanent copies of user-selected files.
- Lazy imports are used so the app can boot without Google libs installed.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
from dataclasses import dataclass
import os
import time
import json
import uuid
"""
Google Drive OAuth and file operations service (lazy-imported deps).

Notes:
- Uses Supabase to persist per-user OAuth credentials in table `google_drive_tokens`.
- Performs OAuth web server flow using google_auth_oauthlib.flow.Flow.
- Scopes limited to drive.file (files created/selected by the app/user).
- Adds support for a server-owned Google Drive using a service account for
    storing permanent copies of user-selected files.
- Lazy imports are used so the app can boot without Google libs installed.
"""
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from supabase import create_client, Client

from app.logging_config import get_logger, bind_logger
from zoneinfo import ZoneInfo
logger = get_logger(__name__)
log = bind_logger(logger)
log.info("started")

# -------- Supabase client (service key) - lazy to avoid startup failures --------
def get_supabase() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise HTTPException(status_code=500, detail="Supabase configuration missing")
    return create_client(url, key)


GOOGLE_CLIENT_SECRET_FILENAME = "client_secret_oauth_apps.googleusercontent.com.json"
GOOGLE_CLIENT_SECRET_PATH = str(Path(__file__).resolve().parent.parent / GOOGLE_CLIENT_SECRET_FILENAME)


# Service account json for server-owned Drive
SERVER_SERVICE_ACCOUNT_FILENAME = "server-client-0961468170-bd62942bd1ac.com.json"
SERVER_SERVICE_ACCOUNT_PATH = str(Path(__file__).resolve().parent.parent / SERVER_SERVICE_ACCOUNT_FILENAME)
# Shared Drive folder where server should store master resumes. Can be overridden via env.
SHARED_DRIVE_FOLDER_ID = os.environ.get("GOOGLE_SHARED_DRIVE_FOLDER_ID", "0ABNYGt-LYK-JUk9PVA")
DRIVE_SCOPES = [
    # Minimal scope to read files the user selects via Picker or
    # any files the user already has access to. No write/create.
    "https://www.googleapis.com/auth/drive.readonly",
]


def _lazy_import_media_download():
        from googleapiclient.http import MediaIoBaseDownload  # type: ignore
        return MediaIoBaseDownload

OAUTH_STATE_SECRET = os.environ.get("OAUTH_STATE_SECRET", "dev-insecure-state-secret")


def _lazy_import_google_flow():
    from google_auth_oauthlib.flow import Flow  # type: ignore
    return Flow


def _lazy_import_google_requests():
    from google.auth.transport.requests import Request  # type: ignore
    return Request


def _lazy_import_build():
    from googleapiclient.discovery import build  # type: ignore
    return build


def _lazy_import_media_upload():
    from googleapiclient.http import MediaIoBaseUpload  # type: ignore
    return MediaIoBaseUpload


def _lazy_import_credentials():
    from google.oauth2.credentials import Credentials  # type: ignore
    return Credentials


def _lazy_import_service_account_credentials():
    from google.oauth2.service_account import Credentials as SACredentials  # type: ignore
    return SACredentials


def sign_state(payload: Dict[str, Any], ttl_seconds: int = 600) -> str:
    """Create a signed, base64-encoded state string with expiry.

    payload is augmented with exp timestamp. Returns url-safe base64 string of JSON + HMAC.
    """
    data = dict(payload)
    data["exp"] = int(time.time()) + ttl_seconds
    raw = json.dumps(data, separators=(",", ":")).encode()
    mac = hmac.new(OAUTH_STATE_SECRET.encode(), raw, hashlib.sha256).digest()
    token = base64.urlsafe_b64encode(raw + b"." + mac).decode()
    return token


def verify_state(token: str) -> Dict[str, Any]:
    try:
        blob = base64.urlsafe_b64decode(token.encode())
        raw, mac = blob.rsplit(b".", 1)
        expected = hmac.new(OAUTH_STATE_SECRET.encode(), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, mac):
            raise ValueError("invalid signature")
        data = json.loads(raw.decode())
        if int(time.time()) > int(data.get("exp", 0)):
            raise ValueError("state expired")
        return data
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid state: {exc}")


# ---------------- Credentials persistence ----------------

TOK_TABLE = "google_drive_tokens"


def save_credentials(user_id: str, credentials_json: Dict[str, Any]) -> None:
    """Upsert credentials for user into Supabase."""
    try:
        # Upsert by user_id
        get_supabase().table(TOK_TABLE).upsert(
            {
                "user_id": str(user_id),
                "credentials": credentials_json,
                "updated_at": int(time.time()),
            },
            on_conflict="user_id",
        ).execute()
    except Exception as e:
        log.error(f"Failed to persist credentials for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to persist credentials: {e}")


def load_credentials(user_id: str):
    """Load credentials for user and return google.oauth2.credentials.Credentials.

    Refreshes tokens if needed and persists the refreshed tokens.
    """
    Credentials = _lazy_import_credentials()
    Request = _lazy_import_google_requests()
    try:
        res = (
            get_supabase().table(TOK_TABLE)
            .select("credentials")
            .eq("user_id", str(user_id))
            .maybe_single()
            .execute()
        )
        row = getattr(res, "data", None) or {}
        info = row.get("credentials") if isinstance(row, dict) else None
        if not info:
            raise HTTPException(status_code=401, detail="Google Drive not authorized for this user")

        creds = Credentials.from_authorized_user_info(info, scopes=DRIVE_SCOPES)
        if not creds.valid:
            if creds.refresh_token:
                creds.refresh(Request())
                # Persist refreshed tokens
                save_credentials(user_id, json.loads(creds.to_json()))
            else:
                raise HTTPException(status_code=401, detail="Missing refresh token; re-authorize required")
        return creds
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Failed to load credentials for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load credentials: {e}")


# ---------------- OAuth Utilities ----------------

def build_flow(redirect_uri: str):
    Flow = _lazy_import_google_flow()
    flow = Flow.from_client_secrets_file(
        GOOGLE_CLIENT_SECRET_PATH,
        scopes=DRIVE_SCOPES,
        redirect_uri=redirect_uri,
    )
    return flow


# ---------------- Drive/Docs operations ----------------

def build_drive_service(credentials) -> Any:
    build = _lazy_import_build()
    return build("drive", "v3", credentials=credentials)


def build_docs_service(credentials) -> Any:
    build = _lazy_import_build()
    return build("docs", "v1", credentials=credentials)


# ---------------- Server (service account) Drive helpers ----------------

# Full Drive scope for the server so it can create, copy, and convert files in its own Drive.
SERVER_DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
]


def get_service_account_credentials():
    """Load service account credentials for the server-owned Drive.

    The service account file path can be overridden with env GOOGLE_SERVER_SERVICE_ACCOUNT_PATH.
    """
    SACredentials = _lazy_import_service_account_credentials()
    json_path = os.environ.get("GOOGLE_SERVER_SERVICE_ACCOUNT_PATH", SERVER_SERVICE_ACCOUNT_PATH)
    try:
        creds = SACredentials.from_service_account_file(json_path, scopes=SERVER_DRIVE_SCOPES)
        return creds
    except Exception as e:
        log.error(f"Failed to load service account credentials: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load server Drive credentials: {e}")

def build_server_drive_service() -> Any:
    """Build a Drive service client authenticated as the server's service account."""
    creds = get_service_account_credentials()
    return build_drive_service(creds)

def copy_file_to_server_drive(server_drive_service, source_file_id: str, new_name: str) -> Dict[str, Any]:
    """Copy a file the service account can access into the server's Drive.

    Returns the created file's JSON (contains at least id and name).
    """
    try:
        # Copy into the shared drive folder and mark support for shared drives
        body = {"name": new_name, "parents": [SHARED_DRIVE_FOLDER_ID]}
        # supportsAllDrives allows copying from shared drives if applicable
        new_file = (
            server_drive_service.files()
            .copy(fileId=source_file_id, body=body, supportsAllDrives=True, fields="id, name, mimeType, parents")
            .execute()
        )
        return new_file
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to copy file to server Drive: {e}")


def download_file_bytes(drive_service, file_id: str) -> bytes:
    """Download raw bytes of a file (non-Google types) from Drive."""
    MediaIoBaseDownload = _lazy_import_media_download()
    import io

    try:
        # include supportsAllDrives=True in case the file lives on a shared drive
        request = drive_service.files().get_media(fileId=file_id, supportsAllDrives=True)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        return fh.getvalue()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to download file: {e}")


def get_file_metadata(drive_service, file_id: str, fields: str = "id, name, mimeType") -> Dict[str, Any]:
    try:
        # Always allow reading metadata from shared drives as well
        return drive_service.files().get(fileId=file_id, fields=fields, supportsAllDrives=True).execute()
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Failed to read file metadata: {e}")


def upload_bytes_as_google_doc(server_drive_service, content: bytes, source_mime: str, name: str) -> Dict[str, Any]:
    """Upload given bytes to the server Drive and convert into a Google Doc.

    The file is created with mimeType=application/vnd.google-apps.document to trigger conversion.
    """
    MediaIoBaseUpload = _lazy_import_media_upload()
    import io

    try:
        media = MediaIoBaseUpload(io.BytesIO(content), mimetype=source_mime, resumable=False)
        # Place the created Google Doc inside the designated Shared Drive folder so the
        # server's service account (content manager) owns/manages the stored resume.
        body = {
            "name": name,
            "mimeType": "application/vnd.google-apps.document",
            "parents": [SHARED_DRIVE_FOLDER_ID],
        }
        created = (
            server_drive_service.files()
            .create(body=body, media_body=media, fields="id, name, mimeType", supportsAllDrives=True)
            .execute()
        )
        return created
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload and convert to Google Doc: {e}")


def upload_bytes_raw(server_drive_service, content: bytes, source_mime: str, name: str) -> Dict[str, Any]:
    """Upload given bytes to the server Drive without conversion (keeps original mime)."""
    MediaIoBaseUpload = _lazy_import_media_upload()
    import io

    try:
        media = MediaIoBaseUpload(io.BytesIO(content), mimetype=source_mime, resumable=False)
        # Upload into the shared drive folder to avoid quota and ownership issues.
        body = {"name": name, "parents": [SHARED_DRIVE_FOLDER_ID]}
        created = (
            server_drive_service.files()
            .create(body=body, media_body=media, fields="id, name, mimeType", supportsAllDrives=True)
            .execute()
        )
        return created
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload file to server Drive: {e}")


def upsert_profile_master_resume_id(user_id: str, dest_file_id: str) -> None:
    """Write the destination Google Drive fileId into profiles.gdrive_master_resume_id."""
    try:
        (
            get_supabase()
            .table("profiles")
            .upsert({"id": str(user_id), "gdrive_master_resume_id": str(dest_file_id)})
            .execute()
        )
    except Exception as e:
        log.error(f"Failed to update profile gdrive_master_resume_id for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update profile: {e}")


def delete_file(drive_service, file_id: str) -> None:
    """Delete a file from Drive (supports shared drives)."""
    try:
        drive_service.files().delete(fileId=file_id, supportsAllDrives=True).execute()
    except Exception as e:
        # Not fatal for our flow; log a warning-like error
        log.error(f"Failed to delete temporary file {file_id}: {e}")


def export_google_doc_text(drive_service, file_id: str) -> str:
    try:
        # First check the file metadata to determine how to fetch it.
        meta = drive_service.files().get(fileId=file_id, fields="mimeType", supportsAllDrives=True).execute()
        mime = meta.get("mimeType", "")
        # Google Docs -> use export
        if mime == "application/vnd.google-apps.document":
            try:
                # Preferred: include supportsAllDrives for shared-drive support
                data = drive_service.files().export(fileId=file_id, mimeType="text/plain", supportsAllDrives=True).execute()
            except TypeError:
                # Some googleapiclient versions don't accept supportsAllDrives on export()
                # Retry without the kwarg (best-effort fallback).
                log.info("export() does not accept supportsAllDrives param, retrying without it", extra={"file_id": file_id})
                data = drive_service.files().export(fileId=file_id, mimeType="text/plain").execute()
        else:
            # Non-Google files -> download media. alt="media" returns bytes for binary/text files.
            try:
                data = drive_service.files().get(fileId=file_id, alt="media", supportsAllDrives=True).execute()
            except TypeError:
                # Fallback if the client doesn't accept supportsAllDrives here (rare)
                log.info("get(..., alt='media') does not accept supportsAllDrives param, retrying without it", extra={"file_id": file_id})
                data = drive_service.files().get(fileId=file_id, alt="media").execute()

        if isinstance(data, bytes):
            return data.decode("utf-8", errors="ignore")
        if isinstance(data, str):
            return data
        return ""
    except Exception as e:
        # Surface a clearer error to the caller including file id and known mime (if available).
        try:
            file_hint = f" fileId={file_id} mime={mime}"
        except Exception:
            file_hint = f" fileId={file_id}"
        raise HTTPException(status_code=404, detail=f"Unable to export file content:{file_hint}: {e}")


def export_google_doc_bytes(drive_service, file_id: str, export_mime: str) -> bytes:
    """Export a Google Doc to the specified mime type and return raw bytes."""
    try:
        # Call export without supportsAllDrives for compatibility with googleapiclient versions
        # that do not accept that kwarg on files().export().
        data = drive_service.files().export(fileId=file_id, mimeType=export_mime).execute()

        if isinstance(data, bytes):
            return data
        if isinstance(data, str):
            return data.encode("utf-8")
        return b""
    except Exception as e:
        # Bubble a clearer message so the router can log context
        raise HTTPException(status_code=500, detail=f"Failed to export Google Doc as {export_mime}: {e}")


def _extract_text_with_index_map(doc: Dict[str, Any]) -> Tuple[str, List[int]]:
    """Return the document text and a map from text index to Docs API index."""
    chars: List[str] = []
    indices: List[int] = []

    def visit(elements: Optional[List[Dict[str, Any]]]) -> None:
        if not elements:
            return
        for element in elements:
            paragraph = element.get("paragraph")
            if paragraph:
                for para_element in paragraph.get("elements", []):
                    start = para_element.get("startIndex")
                    text_run = para_element.get("textRun")
                    if start is None or not text_run:
                        continue
                    text = text_run.get("content", "")
                    for offset, ch in enumerate(text):
                        chars.append(ch)
                        indices.append(start + offset)
            table = element.get("table")
            if table:
                for row in table.get("tableRows", []):
                    for cell in row.get("tableCells", []):
                        visit(cell.get("content"))
            table_of_contents = element.get("tableOfContents")
            if table_of_contents:
                visit(table_of_contents.get("content"))

    visit(doc.get("body", {}).get("content"))
    return "".join(chars), indices


def _find_text_occurrences(doc: Dict[str, Any], search_text: str) -> List[Tuple[int, int]]:
    if not search_text:
        return []
    full_text, index_map = _extract_text_with_index_map(doc)
    occurrences: List[Tuple[int, int]] = []
    start_pos = 0
    while True:
        pos = full_text.find(search_text, start_pos)
        if pos == -1:
            break
        start_index = index_map[pos]
        end_index = index_map[pos + len(search_text) - 1] + 1
        occurrences.append((start_index, end_index))
        start_pos = pos + len(search_text)
    return occurrences


def update_file_content(
    drive_service,
    docs_service,
    file_id: str,
    search_text: str,
    replace_text: str,
    replace_all: bool = False,
) -> Dict[str, Any]:
    """Replace text within a Google Doc while preserving formatting."""
    if not search_text:
        raise HTTPException(status_code=400, detail="search_text is required")
    try:
        meta = (
            drive_service.files()
            .get(fileId=file_id, fields="id, name, mimeType", supportsAllDrives=True)
            .execute()
        )
        mime = meta.get("mimeType")
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"File not found: {e}")

    if mime != "application/vnd.google-apps.document":
        raise HTTPException(status_code=400, detail="Search/replace is only supported for Google Docs")

    try:
        # Docs API doesn't accept supportsAllDrives; access is governed by Drive permissions
        doc = docs_service.documents().get(documentId=file_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load document: {e}")

    occurrences = _find_text_occurrences(doc, search_text)
    if not occurrences:
        return {"updated": False, "matches": 0, "message": "Text not found"}

    try:
        if replace_all:
            requests = [
                {
                    "replaceAllText": {
                        "containsText": {"text": search_text, "matchCase": True},
                        "replaceText": replace_text,
                    }
                }
            ]
            result = (
                docs_service.documents()
                .batchUpdate(documentId=file_id, body={"requests": requests})
                .execute()
            )
            return {"updated": True, "matches": len(occurrences), "method": "replaceAllText", "result": result}

        # Replace only the first occurrence using a temporary named range to preserve formatting.
        start_index, end_index = occurrences[0]
        range_name = f"replace_{uuid.uuid4().hex}"
        requests = [
            {
                "createNamedRange": {
                    "name": range_name,
                    "range": {"startIndex": start_index, "endIndex": end_index},
                }
            },
            {
                "replaceNamedRangeContent": {
                    "namedRangeName": range_name,
                    "text": replace_text,
                }
            },
            {"deleteNamedRange": {"name": range_name}},
        ]
        result = (
            docs_service.documents()
            .batchUpdate(documentId=file_id, body={"requests": requests})
            .execute()
        )
        return {"updated": True, "matches": 1, "method": "replaceNamedRangeContent", "result": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update Google Doc: {e}")


def basic_analyze_text(text: str) -> Dict[str, Any]:
    """Very simple analysis: word count and naive keyword frequency."""
    import re
    from collections import Counter

    words = re.findall(r"[A-Za-z']+", text.lower())
    stop = {
        "the","a","an","and","or","but","is","are","to","of","in","on","for","with","as","by","at","it","this","that","be","was","were","from","your","you","we","our",
    }
    filtered = [w for w in words if w not in stop and len(w) > 2]
    counts = Counter(filtered)
    keywords = counts.most_common(10)
    return {
        "word_count": len(words),
        "unique_words": len(set(filtered)),
        "top_keywords": keywords,
    }

