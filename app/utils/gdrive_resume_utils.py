"""
Utilities for working with Google Drive resumes.

Functions:
- duplicate_master_resume(user_id, duplicate_name):
    Creates a copy of the user's master resume (profiles.gdrive_master_resume_id)
    into the server's Shared Drive with a new name. Returns the created file
    metadata {id, name, mimeType, parents}.

- replace_text_in_doc(file_id, original_text, final_text, replace_all=False):
    Finds and replaces text in a Google Doc while preserving formatting. Uses
    Docs API. Returns a small result dict {updated, matches, method, result?}.

- export_doc_to_pdf(file_id, pdf_name=None):
    Exports a Google Doc to PDF, uploads it to the server Shared Drive, and
    returns the fileId of the uploaded PDF.

Notes:
- These utilities operate using the server service account. Files should be in
  or copied to the server's Shared Drive for consistent access.
- Search/replace is supported for Google Docs only (mimeType application/vnd.google-apps.document).
"""
from __future__ import annotations

from typing import Dict, Optional

from fastapi import HTTPException

from app.logging_config import get_logger, bind_logger
from app.services import google_drive_service as gds

logger = get_logger(__name__)
log = bind_logger(logger, {"agent": "gdrive_resume_utils"})


def _get_profile_master_resume_id(user_id: str) -> Optional[str]:
    """Fetch profiles.gdrive_master_resume_id from Supabase.

    Returns the id as a string if present, else None.
    """
    try:
        sb = gds.get_supabase()
        res = (
            sb.table("profiles")
            .select("gdrive_master_resume_id")
            .eq("id", str(user_id))
            .maybe_single()
            .execute()
        )
        row = getattr(res, "data", None) or {}
        return row.get("gdrive_master_resume_id") if isinstance(row, dict) else None
    except Exception as e:
        log.error(f"Failed to fetch profile for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to read profile: {e}")


def duplicate_master_resume(user_id: str, duplicate_name: str) -> Dict[str, str]:
    """Create a duplicate of the user's master resume on the server's Drive.

    Args:
        user_id: The user whose profile holds gdrive_master_resume_id
        duplicate_name: The new file name for the copy

    Returns:
        Dict with at least {id, name, mimeType, parents}
    """
    if not duplicate_name:
        raise HTTPException(status_code=400, detail="duplicate_name is required")

    master_id = _get_profile_master_resume_id(user_id)
    if not master_id:
        raise HTTPException(status_code=404, detail="No master resume found for this user")

    drive = gds.build_server_drive_service()

    # Validate source exists and is readable first (optional but clearer errors)
    try:
        _ = gds.get_file_metadata(drive, master_id, fields="id, name, mimeType, parents")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Master resume not accessible: {e}")

    # Copy the file into the Shared Drive with the requested name
    created = gds.copy_file_to_server_drive(drive, master_id, duplicate_name)
    log.info("Created duplicate resume on Drive", extra={"user_id": user_id, "new_file": created})
    return created


def replace_text_in_doc(
    file_id: str,
    original_text: str,
    final_text: str,
    replace_all: bool = False,
) -> Dict[str, object]:
    """Find and replace text within a Google Doc while preserving formatting.

    Uses the server service account. The document must be a Google Doc.
    """
    if not file_id:
        raise HTTPException(status_code=400, detail="file_id is required")
    if not original_text:
        raise HTTPException(status_code=400, detail="original_text is required")

    creds = gds.get_service_account_credentials()
    drive = gds.build_drive_service(creds)
    docs = gds.build_docs_service(creds)

    # Delegate to the existing robust updater
    return gds.update_file_content(
        drive_service=drive,
        docs_service=docs,
        file_id=file_id,
        search_text=original_text,
        replace_text=final_text or "",
        replace_all=replace_all,
    )


def prepend_text_to_doc_top(file_id: str, text: str) -> dict:
    """Insert text at the very beginning of a Google Doc (index 1).

    Adds two newlines after the inserted block to separate from existing content.
    """
    if not file_id:
        raise HTTPException(status_code=400, detail="file_id is required")
    if not text:
        return {"updated": False, "message": "No text to insert"}

    creds = gds.get_service_account_credentials()
    drive = gds.build_drive_service(creds)
    docs = gds.build_docs_service(creds)

    # Ensure it's a Google Doc
    meta = gds.get_file_metadata(drive, file_id, fields="id, mimeType")
    if meta.get("mimeType") != "application/vnd.google-apps.document":
        raise HTTPException(status_code=400, detail="Insert supported only for Google Docs")

    try:
        requests = [
            {
                "insertText": {
                    "location": {"index": 1},
                    "text": f"{text}\n\n",
                }
            }
        ]
        result = (
            docs.documents()
            .batchUpdate(documentId=file_id, body={"requests": requests})
            .execute()
        )
        return {"updated": True, "method": "insertText", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to insert text at top: {e}")


def export_doc_to_pdf(file_id: str, pdf_name: Optional[str] = None) -> str:
    """Export a Google Doc to PDF, upload it to the Shared Drive, and return the PDF fileId.

    If pdf_name is not provided, uses the source doc's name + ".pdf".
    """
    if not file_id:
        raise HTTPException(status_code=400, detail="file_id is required")

    drive = gds.build_server_drive_service()

    # Determine base name and verify it's a Google Doc
    meta = gds.get_file_metadata(drive, file_id, fields="id, name, mimeType, parents")
    mime = meta.get("mimeType")
    if mime != "application/vnd.google-apps.document":
        raise HTTPException(status_code=400, detail="PDF export only supported for Google Docs")

    base_name = meta.get("name", "document")
    out_name = pdf_name or f"{base_name}.pdf"

    # Export bytes from Docs API and then upload as a new PDF file into Shared Drive
    pdf_bytes = gds.export_google_doc_bytes(drive, file_id, "application/pdf")
    created = gds.upload_bytes_raw(drive, pdf_bytes, "application/pdf", out_name)
    pdf_id = created.get("id")
    if not isinstance(pdf_id, str) or not pdf_id:
        raise HTTPException(status_code=500, detail="Failed to create PDF on Drive")
    log.info("Exported Google Doc to PDF", extra={"source": file_id, "pdf_id": pdf_id})
    return pdf_id
