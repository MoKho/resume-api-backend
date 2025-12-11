from __future__ import annotations

from typing import Tuple

from fastapi import HTTPException

from app.services import google_drive_service as gds
from app.logging_config import get_logger, bind_logger

logger = get_logger(__name__)

# Google Doc mime
GOOGLE_DOC = "application/vnd.google-apps.document"

# Supported export formats for Google Docs with their mime types and file extensions
EXPORT_FORMATS = {
    "pdf": {"mime": "application/pdf", "ext": ".pdf", "mode": "bytes"},
    "docx": {"mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "ext": ".docx", "mode": "bytes"},
    "odt": {"mime": "application/vnd.oasis.opendocument.text", "ext": ".odt", "mode": "bytes"},
    "rtf": {"mime": "application/rtf", "ext": ".rtf", "mode": "bytes"},
    "txt": {"mime": "text/plain", "ext": ".txt", "mode": "text"},
    # "html" exports as a zipped web page per Drive API
    "html": {"mime": "application/zip", "ext": ".zip", "mode": "bytes"},
    "epub": {"mime": "application/epub+zip", "ext": ".epub", "mode": "bytes"},
    # Markdown is supported per Drive API documentation
    "md": {"mime": "text/markdown", "ext": ".md", "mode": "bytes"},
    "markdown": {"mime": "text/markdown", "ext": ".md", "mode": "bytes"},
}


def _export_filename(base_name: str, desired_ext: str) -> str:
    name = (base_name or "").replace("\n", "").strip()
    if not name:
        name = "export"
    if not name.lower().endswith(desired_ext):
        name = f"{name}{desired_ext}"
    return name


def export_application_bytes(application_id: int, user_id: str, fmt: str) -> Tuple[bytes, str, str]:
    """Return (data, content_type, filename) for an export format.

    Raises HTTPException on errors (404/403/415/409/500) as appropriate.
    """
    log = bind_logger(logger, {"agent": "export_service", "application_id": application_id, "user_id": str(user_id), "format": fmt})
    fmt_key = (fmt or "").lower()
    cfg = EXPORT_FORMATS.get(fmt_key)
    if not cfg:
        log.warning("Unsupported export format requested")
        raise HTTPException(status_code=415, detail=f"Unsupported export format: {fmt}")

    sb = gds.get_supabase()
    app_row = sb.table("applications").select("*").eq("id", application_id).single().execute().data
    if not app_row:
        log.error("Application not found for export")
        raise HTTPException(status_code=404, detail="Application not found")
    if app_row.get("user_id") != str(user_id):
        log.warning("Unauthorized export attempt")
        raise HTTPException(status_code=403, detail="Not authorized to export this application")

    doc_id = app_row.get("gdrive_doc_resume_id")
    if not doc_id:
        log.error("Missing gdrive_doc_resume_id; cannot export")
        raise HTTPException(status_code=404, detail="No source Google Doc available for export")

    drive = gds.build_server_drive_service()
    meta = gds.get_file_metadata(drive, doc_id, fields="id, name, mimeType, size")
    src_mime = meta.get("mimeType")
    src_name = meta.get("name") or f"application-{application_id}"
    log.info("Loaded source doc for export", extra={"doc_id": doc_id, "src_mime": src_mime, "src_name": src_name})

    export_mime = cfg["mime"]
    ext = cfg["ext"]
    mode = cfg["mode"]

    # If the source is not a Google Doc, only allow download when the requested format matches the source mime
    if src_mime != GOOGLE_DOC and export_mime != src_mime:
        log.warning("Source is not a Google Doc; requested conversion unsupported", extra={"requested": export_mime})
        raise HTTPException(status_code=409, detail="Source file is not a Google Doc; cannot export to requested format")

    if mode == "text":
        log.info("Exporting Google Doc as plain text")
        content = gds.export_google_doc_text(drive, doc_id)
        data = content.encode("utf-8")
    else:
        log.info("Exporting Google Doc as bytes", extra={"export_mime": export_mime})
        data = gds.export_google_doc_bytes(drive, doc_id, export_mime)

    filename = _export_filename(src_name, ext)
    log.info("Export completed", extra={"filename": filename, "export_mime": export_mime, "size": len(data)})
    return data, export_mime, filename


def head_export_check(application_id: int, user_id: str, fmt: str) -> None:
    fmt_key = (fmt or "").lower()
    cfg = EXPORT_FORMATS.get(fmt_key)
    if not cfg:
        raise HTTPException(status_code=415, detail=f"Unsupported export format: {fmt}")

    sb = gds.get_supabase()
    app_row = sb.table("applications").select("id,user_id,gdrive_doc_resume_id").eq("id", application_id).single().execute().data
    if not app_row or app_row.get("user_id") != str(user_id):
        raise HTTPException(status_code=404, detail="Not found")
    if not app_row.get("gdrive_doc_resume_id"):
        raise HTTPException(status_code=404, detail="No source document")

    drive = gds.build_server_drive_service()
    meta = gds.get_file_metadata(drive, app_row["gdrive_doc_resume_id"], fields="id, mimeType")
    src_mime = meta.get("mimeType")
    export_mime = cfg["mime"]
    if src_mime != GOOGLE_DOC and export_mime != src_mime:
        raise HTTPException(status_code=409, detail="Source not convertible to requested format")
