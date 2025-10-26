from __future__ import annotations

import os
# Allow HTTP in development for oauthlib (avoid in production)
if os.getenv("ENV", "development") != "production":
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

from typing import Any, Dict, Optional
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse

from app.security import get_current_user
from app.services.google_drive_service import (
    build_flow,
    sign_state,
    verify_state,
    save_credentials,
    load_credentials,
    build_drive_service,
    export_google_doc_text,
    build_server_drive_service,
    get_file_metadata,
    download_file_bytes,
    upload_bytes_as_google_doc,
    upload_bytes_raw,
    upsert_profile_master_resume_id,
    delete_file,
    export_google_doc_bytes,
)
from app.models.schemas import (
    GoogleDriveOpenFileRequest,
    GoogleDriveOpenFileResponse,
    GoogleDriveFileRef,
)
from app.logging_config import get_logger, bind_logger

logger = get_logger(__name__)

router = APIRouter(tags=["google-drive"])

def _popup_close_page(status: str, user_id: str | None, origin: str, error: str | None = None, retryable: bool = False) -> str:
    # Very small page that notifies the opener and closes itself.
    safe_status = (status or "error").replace('"', "")
    safe_user = (user_id or "").replace('"', "")
    safe_origin = (origin or "").replace('"', "")
    safe_err = (error or "").replace('"', "")
    retry_flag = "true" if retryable else "false"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Google Drive Auth</title></head>
<body>
<script>
(function() {{
  var data = {{
    type: "google-drive-auth",
    status: "{safe_status}",
    userId: "{safe_user}",
    error: "{safe_err}",
    retryable: {retry_flag}
  }};
  try {{
    if (window.opener && "{safe_origin}") {{
      window.opener.postMessage(data, "{safe_origin}");
    }}
  }} catch (e) {{}}
  window.close();
  // Fallback if window cannot close
  setTimeout(function() {{
    document.body.textContent = "You can close this window.";
  }}, 500);
}})();
</script>
</body></html>"""


@router.get("/authorize")
async def authorize(request: Request, user=Depends(get_current_user)):
    """Return a Google OAuth consent URL for Drive (drive.file scope)."""
    # Determine redirect_uri based on environment
    redirect_uri = os.environ.get(
        "GOOGLE_OAUTH_REDIRECT_URI",
        "http://localhost:8000/google-drive/oauth2callback",
    )

    flow = build_flow(redirect_uri)

    # Determine the frontend origin to notify after auth
    # Prefer the Origin header; fallback to env FRONTEND_ORIGIN.
    frontend_origin = request.headers.get("origin") or os.environ.get("FRONTEND_ORIGIN", "http://localhost:5173")

    # Encode user and origin in state to resume after callback
    state = sign_state({"user_id": str(user.id), "origin": frontend_origin})
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return {"authorization_url": auth_url}


@router.get("/oauth2callback")
async def oauth2callback(request: Request, state: str = Query(...), code: str = Query(None)):
    """Handle Google OAuth redirect: exchange code for tokens and persist them."""
    redirect_uri = os.environ.get(
        "GOOGLE_OAUTH_REDIRECT_URI",
        "http://localhost:8000/google-drive/oauth2callback",
    )

    # Log and handle invalid/expired state
    try:
        data = verify_state(state)
    except HTTPException as e:
        logger.error("OAuth state verification failed", extra={"detail": e.detail})
        # invalid signature / expired = retryable (frontend should fetch a fresh /authorize)
        html = _popup_close_page("error", None, os.environ.get("FRONTEND_ORIGIN", "http://localhost:5173"), str(e.detail), True)
        return HTMLResponse(content=html, media_type="text/html", status_code=400)

    user_id = data.get("user_id")
    origin = data.get("origin") or os.environ.get("FRONTEND_ORIGIN", "http://localhost:5173")
    if not user_id:
        html = _popup_close_page("error", None, origin, "Missing user_id in state", False)
        return HTMLResponse(content=html, media_type="text/html", status_code=400)

    try:
        flow = build_flow(redirect_uri)
        flow.fetch_token(authorization_response=str(request.url))
        creds = flow.credentials
        save_credentials(user_id, json.loads(creds.to_json()))
        html = _popup_close_page("ok", user_id, origin, None)
        return HTMLResponse(content=html, media_type="text/html")
    except Exception as e:
        logger.exception("OAuth token exchange failed", extra={"user_id": user_id, "redirect_uri": redirect_uri})
        # token exchange errors are generally non-retryable from the frontend; surface message
        html = _popup_close_page("error", user_id, origin, f"OAuth exchange failed: {e}", False)
        return HTMLResponse(content=html, media_type="text/html", status_code=400)

    # Redirect back to the app (optional). For API, just return success
    return JSONResponse({"status": "ok", "user_id": user_id})


@router.post("/open-file", response_model=GoogleDriveOpenFileResponse)
async def open_file(payload: GoogleDriveOpenFileRequest, user=Depends(get_current_user)):
    """Copy a user's selected Drive file into the server's Drive and return its contents.

    Flow:
    - Validate input and load user's OAuth credentials (read-only access to user's Drive).
    - Build server's Drive client using the service account (full access to server Drive).
        - Determine source file mimeType.
            - If Google Doc: export as DOCX bytes, then upload to server Drive converting back to Google Doc (best fidelity without sharing).
            - If DOC/DOCX: download bytes from user's Drive and upload to server Drive without conversion (exact copy). Use a temporary conversion only for text extraction.
            - Otherwise (e.g., PDF): download bytes and upload converting to Google Doc.
    - Store the destination fileId in Supabase profiles.gdrive_master_resume_id.
    - Export destination content as text and return to caller. Optionally apply find/replace on the destination.
    """
    # Extract request parameters
    file_id = payload.fileId

    # Prepare structured logger with request context
    log = bind_logger(logger, {"agent": "open_file", "user_id": str(user.id), "source_file_id": file_id})
    log.info("open_file: start")

    # Build user and server Drive clients
    user_creds = load_credentials(str(user.id))
    user_drive = build_drive_service(user_creds)
    server_drive = build_server_drive_service()

    # Identify source file type
    src_meta = get_file_metadata(user_drive, file_id, fields="id, name, mimeType")
    src_mime = src_meta.get("mimeType", "")
    src_name = src_meta.get("name", "")
    log.info("open_file: source metadata loaded", extra={"mime": src_mime, "name": src_name})

    # Destination filename convention
    dest_name = f"{user.id}-master-resume"

    # Prepare variables for destination
    dest_file: Dict[str, Any]
    dest_file_id: str
    dest_mime: str

    # Define helpers for mime checks
    GOOGLE_DOC = "application/vnd.google-apps.document"
    DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    DOC = "application/msword"

    try:
        if src_mime == GOOGLE_DOC:
            # Case 1: Source is a Google Doc. We cannot share/copy, so export as DOCX and re-import as Google Doc.
            DOCX_EXPORT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            log.info("open_file: exporting Google Doc as DOCX bytes for upload")
            docx_bytes = export_google_doc_bytes(user_drive, file_id, DOCX_EXPORT)
            log.info("open_file: uploading exported bytes to server Drive as Google Doc", extra={"dest_name": dest_name})
            dest_file = upload_bytes_as_google_doc(server_drive, docx_bytes, DOCX_EXPORT, dest_name)
            dest_file_id = dest_file["id"]
            dest_mime = dest_file.get("mimeType", GOOGLE_DOC)
        
        elif src_mime in (DOC, DOCX):
            # Case 2: Word docs. Make an exact replica in server Drive (same format) without sharing.
            log.info("open_file: downloading DOC/DOCX bytes from user Drive", extra={"mime": src_mime})
            raw = download_file_bytes(user_drive, file_id)
            log.info("open_file: uploading DOC/DOCX bytes to server Drive without conversion", extra={"dest_name": dest_name})
            dest_file = upload_bytes_raw(server_drive, raw, src_mime, dest_name)
            dest_file_id = dest_file["id"]
            dest_mime = dest_file.get("mimeType", src_mime)
        
        else:
            # Case 3: Other types (e.g., PDF). Download and convert to Google Doc.
            log.info("open_file: downloading non-Doc file bytes from user Drive", extra={"mime": src_mime})
            raw = download_file_bytes(user_drive, file_id)
            # Choose a reasonable source mime for upload; fall back to application/octet-stream
            source_mime = src_mime or "application/octet-stream"
            log.info("open_file: uploading to server Drive as Google Doc (conversion)", extra={"dest_name": dest_name})
            dest_file = upload_bytes_as_google_doc(server_drive, raw, source_mime, dest_name)
            dest_file_id = dest_file["id"]
        dest_mime = dest_file.get("mimeType", GOOGLE_DOC)

        log.info("open_file: destination created", extra={"dest_file_id": dest_file_id, "dest_mime": dest_mime})

        # Persist destination file id to Supabase profile
        upsert_profile_master_resume_id(str(user.id), dest_file_id)
        log.info("open_file: profile updated with gdrive_master_resume_id", extra={"dest_file_id": dest_file_id})

        # Read content to return to the frontend
        if src_mime == GOOGLE_DOC or dest_mime == GOOGLE_DOC:
            # Destination is Google Doc (or started as), export directly
            content = export_google_doc_text(server_drive, dest_file_id)
        elif src_mime in (DOC, DOCX):
            # For DOC/DOCX stored as original in server Drive, create a temporary Google Doc for text extraction
            log.info("open_file: creating temporary Google Doc for text extraction from DOC/DOCX")
            raw = download_file_bytes(user_drive, file_id)
            temp = upload_bytes_as_google_doc(server_drive, raw, src_mime, f"{dest_name}-tmp-conversion")
            try:
                content = export_google_doc_text(server_drive, temp["id"])
            finally:
                delete_file(server_drive, temp["id"])  # best-effort cleanup
        else:
            # Fallback: try exporting whatever we created
            content = export_google_doc_text(server_drive, dest_file_id)

        log.info("open_file: destination content exported")

        result: GoogleDriveOpenFileResponse = GoogleDriveOpenFileResponse(
            source=GoogleDriveFileRef(fileId=file_id, mimeType=src_mime, name=src_name),
            destination=GoogleDriveFileRef(fileId=dest_file_id, mimeType=dest_mime, name=dest_file.get("name", dest_name)),
            content=content,
        )

        log.info("open_file: done")
        return result
    except HTTPException as e:
        log_error = {
            "status_code": e.status_code,
            "detail": getattr(e, "detail", str(e))
        }
        log.error("open_file: failed with HTTPException", extra=log_error)
        raise
    except Exception as e:
        log.error("open_file: unexpected error", extra={"error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to process file: {e}")


@router.get("/auth-status")
async def auth_status(user=Depends(get_current_user)):
    """Check if the user has authenticated their Google account."""
    try:
        creds = load_credentials(str(user.id))
        if creds and creds.valid:
            return {"authenticated": True}
    except Exception:
        pass
    return {"authenticated": False}
