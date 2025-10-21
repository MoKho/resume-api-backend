from __future__ import annotations

import os
from typing import Any, Dict, Optional
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse

from app.security import get_current_user
from app.services.google_drive_service import (
    build_flow,
    export_google_doc_text,
    load_credentials,
    save_credentials,
    sign_state,
    update_file_content,
    verify_state,
    build_drive_service,
    build_docs_service,
    get_supabase,
)
from app.services.google_service_account import (
    build_server_drive_service,
    build_server_docs_service,
)


router = APIRouter(tags=["google-drive"])

# ... existing code ...

@router.post("/set-master-resume-from-drive")
async def set_master_resume_from_drive(payload: Dict[str, Any], user=Depends(get_current_user)):
    """
    Copies a user-selected file to the server's drive to be used as a master resume.
    This will replace any existing master resume (text or GDrive).
    """
    user_file_id = payload.get("fileId")
    if not user_file_id:
        raise HTTPException(status_code=400, detail="fileId is required")

    user_id = str(user.id)
    supabase = get_supabase()

    # 1. Build services for both user and server
    user_creds = load_credentials(user_id)
    user_drive = build_drive_service(user_creds)
    server_drive = build_server_drive_service()

    # 2. Get original file metadata from user's drive
    try:
        original_file_meta = user_drive.files().get(fileId=user_file_id, fields="name").execute()
        original_file_name = original_file_meta.get("name", "untitled")
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Could not access user's file: {e}")

    # 3. Check for and delete any old GDrive master resume for this user
    try:
        profile_res = supabase.table("profiles").select("gdrive_master_resume_id").eq("id", user_id).single().execute()
        if profile_res.data and profile_res.data.get("gdrive_master_resume_id"):
            old_file_id = profile_res.data["gdrive_master_resume_id"]
            try:
                server_drive.files().delete(fileId=old_file_id).execute()
            except Exception as e:
                # Log this error but don't block the flow
                print(f"Warning: Failed to delete old master resume file {old_file_id} for user {user_id}: {e}")
    except Exception as e:
        print(f"Info: Could not check for old master resume for user {user_id}: {e}")


    # 4. Copy the file to the server's drive
    new_file_name = f"{user.email}-template-{original_file_name}"
    copied_file_body = {"name": new_file_name}
    
    server_file_id = None
    try:
        # We need to use the user's drive service to access the source file
        # but the request is executed by the server's drive service to create the copy in its own space.
        # This requires a bit of a workaround. The `copy` method on the service account's
        # drive instance needs permission on the user's file.
        # A simpler approach is to download content and re-upload.

        # Let's get the content from the user's drive first.
        content_bytes = user_drive.files().get_media(fileId=user_file_id).execute()

        # Now create a new file in the server's drive with that content.
        from io import BytesIO
        from googleapiclient.http import MediaIoBaseUpload

        file_metadata = {'name': new_file_name}
        media = MediaIoBaseUpload(BytesIO(content_bytes), mimetype='application/vnd.google-apps.document', resumable=True)
        
        # It seems we can't just upload raw bytes to create a Google Doc.
        # Let's try copying and granting permissions. This is complex.
        
        # Alternative: Copy it, which makes the service account the owner.
        # The service account needs to be granted permission on the source file first.
        # This is not practical.

        # Let's stick to the original idea of copying, but we need to handle permissions.
        # The easiest way is if the service account has broad access, but that's not ideal.

        # Let's try the copy method again, it should work if the file is public or the service account has access.
        # For the `copy` to work, the user must have shared the file with the service account's email address.
        # This is a big UX hurdle.

        # Let's pivot: The user's credentials are used to create a copy, then we transfer ownership to the service account.
        # This also has permission complexities.

        # The most robust flow without major UX changes:
        # 1. User auths (as is).
        # 2. Server (with user creds) reads the file content.
        # 3. Server (with service account creds) creates a NEW file with that content.

        # The `export_google_doc_text` already gets the text content. Let's use that.
        # We will lose formatting, but it's a start.
        text_content = export_google_doc_text(user_drive, user_file_id)

        # Create a new Google Doc on the server's drive with this text.
        docs_service = build_server_docs_service()
        doc_body = {
            'title': new_file_name,
        }
        new_doc = docs_service.documents().create(body=doc_body).execute()
        server_file_id = new_doc['documentId']

        # Now, insert the text content.
        docs_service.documents().batchUpdate(
            documentId=server_file_id,
            body={'requests': [{'insertText': {'location': {'index': 1}, 'text': text_content}}]
        }).execute()

    except Exception as e:
        if server_file_id:
            try:
                server_drive.files().delete(fileId=server_file_id).execute()
            except Exception as cleanup_error:
                print(f"Failed to cleanup partially created file {server_file_id}: {cleanup_error}")
        raise HTTPException(status_code=500, detail=f"Failed to copy file to server drive: {e}")

    # 5. Update the user's profile in Supabase
    try:
        supabase.table("profiles").update({
            "gdrive_master_resume_id": server_file_id,
            "base_resume_text": None  # Clear out the text-based resume
        }).eq("id", user_id).execute()
    except Exception as e:
        # Rollback by deleting the created file
        server_drive.files().delete(fileId=server_file_id).execute()
        raise HTTPException(status_code=500, detail=f"Failed to update profile with new resume file: {e}")

    # 6. Return the content of the new file
    return {
        "message": "Master resume created successfully from Google Drive file.",
        "gdrive_master_resume_id": server_file_id,
        "content": text_content,
    }


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
