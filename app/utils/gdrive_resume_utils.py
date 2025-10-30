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
import re

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


def _flexible_whitespace_pattern() -> str:
    """Regex character class to match any run of whitespace including NBSP/zero-width."""
    return r"[\s\u00A0\u200B]+"


def _pattern_from_block(block: str) -> re.Pattern:
    """Create a whitespace-tolerant regex pattern from a text block.

    Contiguous whitespace in the block is matched by a flexible pattern in the target,
    while all non-whitespace characters are escaped literally.
    """
    parts = []
    i = 0
    while i < len(block):
        ch = block[i]
        if ch.isspace() or ch in ("\u00A0", "\u200B"):
            while i < len(block) and (block[i].isspace() or block[i] in ("\u00A0", "\u200B")):
                i += 1
            parts.append(_flexible_whitespace_pattern())
        else:
            parts.append(re.escape(ch))
            i += 1
    pattern = "".join(parts)
    return re.compile(pattern, flags=re.DOTALL | re.MULTILINE)


def _pattern_from_block_docs(block: str) -> re.Pattern:
    """Docs-focused pattern builder that tolerates list bullets/numbers at line starts.

    For each line:
      - If it starts with a common bullet (*, -, •, – , —) or a numbered list (e.g., 1.  or 2) ),
        we do NOT require those characters to match because Docs represents bullets via styling.
      - Whitespace runs are matched using a flexible pattern so CR/LF/NBSP differences are tolerated.
    """
    bullet_prefix = re.compile(r"^\s*(?:[\*\-•–—]\s+|\d+[\.)]\s+)")
    lines = block.splitlines()
    parts: list[str] = []
    for idx, line in enumerate(lines):
        # Optionally allow bullet prefix but don't require it in the doc text
        m = bullet_prefix.match(line)
        if m:
            # In the doc text, the bullet glyph is not part of text runs; treat as optional (zero width)
            # So we simply ignore the bullet prefix from the pattern and match the remaining text.
            line_body = line[m.end():]
        else:
            line_body = line

        # Convert the line body to a flexible pattern
        j = 0
        while j < len(line_body):
            ch = line_body[j]
            if ch.isspace() or ch in ("\u00A0", "\u200B"):
                while j < len(line_body) and (line_body[j].isspace() or line_body[j] in ("\u00A0", "\u200B")):
                    j += 1
                parts.append(_flexible_whitespace_pattern())
            else:
                parts.append(re.escape(ch))
                j += 1

        # Between lines, allow flexible whitespace (Docs uses paragraph newlines)
        if idx < len(lines) - 1:
            parts.append(_flexible_whitespace_pattern())

    pattern = "".join(parts)
    return re.compile(pattern, flags=re.DOTALL | re.MULTILINE)


def _flatten_doc_text_with_map(document: dict) -> tuple[str, list[dict]]:
    """Flatten the Google Doc body text into a single string and map offsets to doc indices.

    Returns (flat_text, segments) where each segment is a dict with:
      - flat_start, flat_end: offsets in flat_text
      - doc_start, doc_end: corresponding document indices for that text run
    """
    body = (document or {}).get("body", {})
    content = body.get("content", []) or []
    flat_parts: list[str] = []
    segments: list[dict] = []
    flat_offset = 0

    for el in content:
        para = el.get("paragraph")
        if not para:
            # Could be a table or section break; ignore for now
            continue
        for pe in para.get("elements", []) or []:
            text_run = pe.get("textRun")
            if not text_run:
                continue
            text = text_run.get("content") or ""
            if not text:
                continue
            doc_start = pe.get("startIndex")
            doc_end = pe.get("endIndex")
            if not isinstance(doc_start, int) or not isinstance(doc_end, int):
                # If indices are missing, skip mapping this run to avoid corrupting ranges
                continue
            flat_start = flat_offset
            flat_end = flat_start + len(text)
            flat_parts.append(text)
            segments.append({
                "flat_start": flat_start,
                "flat_end": flat_end,
                "doc_start": doc_start,
                "doc_end": doc_end,
                "text": text,
            })
            flat_offset = flat_end

    return ("".join(flat_parts), segments)


def _map_flat_offset_to_doc_index(segments: list[dict], flat_offset: int) -> Optional[int]:
    """Map a flat text offset back to a Google Doc index using the segments map."""
    for seg in segments:
        if seg["flat_start"] <= flat_offset <= seg["flat_end"]:
            # Compute proportional index within this segment
            delta = flat_offset - seg["flat_start"]
            return seg["doc_start"] + delta
    return None


def _range_overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return not (a_end <= b_start or b_end <= a_start)


def _is_list_block(document: dict, doc_start: int, doc_end: int) -> bool:
    """Return True if the range overlaps any paragraph that has bullet styling."""
    for el in (document or {}).get("body", {}).get("content", []) or []:
        para = el.get("paragraph")
        if not para:
            continue
        el_start = el.get("startIndex")
        el_end = el.get("endIndex")
        if isinstance(el_start, int) and isinstance(el_end, int) and _range_overlaps(el_start, el_end, doc_start, doc_end):
            if para.get("bullet") is not None:
                return True
    return False


_LIST_PREFIX_RE = re.compile(r"^\s*(?:[\*\-•–—]\s+|\d+[\.)]\s+)")


def _strip_list_prefixes(text: str) -> str:
    """Remove common bullet/number prefixes from each line of text."""
    lines = text.splitlines()
    stripped = [_LIST_PREFIX_RE.sub("", line) for line in lines]
    return "\n".join(stripped)


def replace_text_block_flexible(
    file_id: str,
    original_text: str,
    final_text: str,
) -> Dict[str, object]:
    """Find the entire original_text in a Doc using whitespace-tolerant matching and replace it.

    This searches the whole original block (not only first/last sentence) using a flexible regex
    that tolerates CR/LF, NBSP, and zero-width spaces. It then replaces the matched range via
    Docs API batchUpdate calls.
    """
    if not file_id:
        raise HTTPException(status_code=400, detail="file_id is required")
    if not original_text:
        return {"updated": False, "matches": 0, "method": "replace_text_block_flexible", "reason": "empty original_text"}

    creds = gds.get_service_account_credentials()
    drive = gds.build_drive_service(creds)
    docs = gds.build_docs_service(creds)

    # Ensure it's a Google Doc
    meta = gds.get_file_metadata(drive, file_id, fields="id, mimeType")
    if meta.get("mimeType") != "application/vnd.google-apps.document":
        raise HTTPException(status_code=400, detail="Search/replace supported only for Google Docs")

    doc = docs.documents().get(documentId=file_id).execute()
    flat_text, segments = _flatten_doc_text_with_map(doc)

    # Build pattern (Docs-aware) and search the entire flat text
    pattern = _pattern_from_block_docs(original_text)
    m = pattern.search(flat_text)
    if not m:
        return {"updated": False, "matches": 0, "method": "replace_text_block_flexible"}

    flat_start, flat_end = m.span()
    # Map to doc indices
    doc_start = _map_flat_offset_to_doc_index(segments, flat_start)
    # For end index in Docs API, use exclusive end
    doc_end = _map_flat_offset_to_doc_index(segments, flat_end)
    if doc_start is None or doc_end is None:
        return {"updated": False, "matches": 0, "method": "replace_text_block_flexible", "reason": "failed to map indices"}

    try:
        # If the original range belonged to a list, strip bullet prefixes and reapply bullet styling
        insert_text = final_text or ""
        make_list = _is_list_block(doc, doc_start, doc_end)
        if make_list:
            insert_text = _strip_list_prefixes(insert_text)

        requests = [
            {"deleteContentRange": {"range": {"startIndex": doc_start, "endIndex": doc_end}}},
            {"insertText": {"location": {"index": doc_start}, "text": insert_text}},
        ]

        # Optionally re-apply bullet styling to the inserted paragraphs
        if make_list and insert_text:
            requests.append({
                "createParagraphBullets": {
                    "range": {"startIndex": doc_start, "endIndex": doc_start + len(insert_text)},
                    "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"
                }
            })
        result = (
            docs.documents()
            .batchUpdate(documentId=file_id, body={"requests": requests})
            .execute()
        )
        return {"updated": True, "matches": 1, "method": "replace_text_block_flexible", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to replace text range: {e}")


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
