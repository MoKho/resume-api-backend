from __future__ import annotations

import os
from typing import Any, Dict, Optional
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.security import get_current_user
from app.services.google_drive_service import (
    build_flow,
    sign_state,
    verify_state,
    save_credentials,
    load_credentials,
    build_drive_service,
    build_docs_service,
    export_google_doc_text,
    update_file_content,
    basic_analyze_text,
)


router = APIRouter(tags=["google-drive"])


@router.get("/authorize")
async def authorize(request: Request, user=Depends(get_current_user)):
    """Return a Google OAuth consent URL for Drive (drive.file scope)."""
    # Determine redirect_uri based on environment
    # Allow overriding via env for production domain
    redirect_uri = os.environ.get(
        "GOOGLE_OAUTH_REDIRECT_URI",
        "http://localhost:8000/google-drive/oauth2callback",
    )

    flow = build_flow(redirect_uri)

    # Encode user and returnUrl (optional) in state to resume after callback
    state = sign_state({"user_id": str(user.id)})
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

    data = verify_state(state)
    user_id = data.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing user_id in state")

    try:
        flow = build_flow(redirect_uri)
        # Build full URL for fetch_token
        # In FastAPI, request.url includes query params
        flow.fetch_token(authorization_response=str(request.url))
        creds = flow.credentials
        # Persist tokens
        save_credentials(user_id, json.loads(creds.to_json()))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"OAuth exchange failed: {e}")

    # Redirect back to the app (optional). For API, just return success
    return JSONResponse({"status": "ok", "user_id": user_id})


@router.post("/open-file")
async def open_file(payload: Dict[str, Any], user=Depends(get_current_user)):
    """Read, analyze, and optionally edit a specific file by fileId.

    Request JSON:
    - fileId: str (required)
    - find: str (optional) text to search for
    - replace: str (optional) replacement text
    - replace_all: bool (optional, default false)
    """
    file_id = payload.get("fileId")
    if not file_id or not isinstance(file_id, str):
        raise HTTPException(status_code=400, detail="fileId is required")

    creds = load_credentials(str(user.id))
    drive = build_drive_service(creds)
    docs = build_docs_service(creds)

    content = export_google_doc_text(drive, file_id)
    #analysis = basic_analyze_text(content)

    result: Dict[str, Any] = {
        "fileId": file_id,
        #"analysis": analysis,
        "content": content,  # contents of the file
    }

    find_text = payload.get("find")
    replace_text = payload.get("replace")
    replace_all = bool(payload.get("replace_all", False))

    if isinstance(find_text, str) and find_text and isinstance(replace_text, str):
        update_res = update_file_content(
            drive,
            docs,
            file_id,
            find_text,
            replace_text,
            replace_all=replace_all,
        )
        result["update"] = update_res

    return result


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
