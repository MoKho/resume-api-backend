from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from app.security import get_current_user
from app.models.schemas import ApplicationCreate, ApplicationResponse
from app.services.resume_service import run_tailoring_process
from app.services import google_drive_service as gds
from app.services.export_service import export_application_bytes, head_export_check
import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv(override=True)

router = APIRouter(
    prefix="/applications",
    tags=["applications"]
)

# Use the service key here for backend operations
supabase_url = os.environ.get("SUPABASE_URL")
supabase_service_key = os.environ.get("SUPABASE_SERVICE_KEY")
# Ensure environment variables are present to satisfy type checkers and avoid runtime None
if not supabase_url or not supabase_service_key:
    raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
supabase: Client = create_client(supabase_url, supabase_service_key)


@router.post("/", response_model=ApplicationResponse, status_code=202)
async def create_application(
    application_data: ApplicationCreate,
    user=Depends(get_current_user)
):
    """
    Create a new resume tailoring application.
    This starts a background task to process the resume.
    """
    # Create the initial application entry in the database
    
    new_app = {
        "user_id": str(user.id),
        "target_job_description": application_data.target_job_description,
        #"job_history_ids": application_data.job_history_ids,
        "status": "pending"
    }
    
    try:
        result = supabase.table("applications").insert(new_app).execute().data
        application_entry = result[0]

        # The application is now enqueued (status="pending").
        # A separate worker process will pick it up and run `run_tailoring_process`.
        return application_entry
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{application_id}", response_model=ApplicationResponse)
async def get_application(application_id: int, user=Depends(get_current_user)):
    """
    Retrieve the status and result of a specific application.
    """
    try:
        result = supabase.table("applications").select("*").eq("id", application_id).single().execute().data
        
        if result['user_id'] != str(user.id):
             raise HTTPException(status_code=403, detail="Not authorized to view this application")

        return result
    except Exception as e:
        raise HTTPException(status_code=404, detail="Application not found")

@router.get(
    "/{application_id}/pdf",
    summary="Download tailored resume as PDF (deprecated)",
    description=(
        "Deprecated: use /applications/{application_id}/export?format=pdf.\n\n"
        "Exports the stored Google Doc source to PDF."
    ),
)
async def download_application_pdf(application_id: int, user=Depends(get_current_user)):
    """
    Stream the tailored resume PDF to the user's browser.
    Requires that the application belongs to the authenticated user and that
    gdrive_pdf_resume_id is present on the application row.
    """
    # Deprecated: proxy to export API (pdf)
    data, content_type, filename = export_application_bytes(application_id, str(user.id), "pdf")
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "private, no-store",
        "Warning": '299 - "Deprecated: use /export?format=pdf"',
    }
    return Response(content=data, media_type=content_type, headers=headers)

@router.head(
    "/{application_id}/pdf",
    summary="Check PDF export readiness (deprecated)",
    description=(
        "Deprecated: use /applications/{application_id}/export?format=pdf (HEAD)."
    ),
)
async def head_application_pdf(application_id: int, user=Depends(get_current_user)):
    """
    Lightweight readiness check for the tailored PDF.
    - 204 No Content if available
    - 404 if not available or not authorized
    """
    # Deprecated: proxy to export API (pdf)
    head_export_check(application_id, str(user.id), "pdf")
    headers = {"Warning": '299 - "Deprecated: use /export?format=pdf"'}
    return Response(status_code=204, headers=headers)

@router.get(
    "/{application_id}/export",
    summary="Export tailored resume in various formats",
    description=(
        "Export the stored Google Doc source to a chosen format.\n\n"
        "Supported formats (Drive export MIME types):\n"
        "- pdf: application/pdf (.pdf)\n"
        "- docx: application/vnd.openxmlformats-officedocument.wordprocessingml.document (.docx)\n"
        "- odt: application/vnd.oasis.opendocument.text (.odt)\n"
        "- rtf: application/rtf (.rtf)\n"
        "- txt: text/plain (.txt)\n"
        "- html: application/zip (.zip) – Web Page (HTML) bundle\n"
        "- epub: application/epub+zip (.epub)\n"
        "- md/markdown: text/markdown (.md)\n"
        "Note: Conversion requires a Google Doc as the source."
    ),
)
async def export_application(
    application_id: int,
    format: str = Query(..., description="Export format: pdf, docx, odt, rtf, txt, html, epub, md/markdown"),
    user=Depends(get_current_user),
):
    """
    Export the tailored resume Google Doc to a chosen format.
    Supported formats map to Drive export MIME types: pdf, docx, odt, rtf, txt, html(zip), epub, md(markdown).
    """
    data, content_type, filename = export_application_bytes(application_id, str(user.id), format)
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "private, no-store",
    }
    return Response(content=data, media_type=content_type, headers=headers)

@router.head(
    "/{application_id}/export",
    summary="Export readiness check",
    description=(
        "HEAD readiness for export availability for a given format. \n"
        "Returns: 204 when available; 404 when not found/unauthorized;\n"
        "415 when format unsupported; 409 when source not convertible."
    ),
)
async def head_export_application(
    application_id: int,
    format: str = Query(..., description="Export format: pdf, docx, odt, rtf, txt, html, epub, md/markdown"),
    user=Depends(get_current_user),
):
    """
    Readiness check for export availability for a given format.
    - 204 when available
    - 404 when not found or unauthorized
    - 415 when format is unsupported
    - 409 when source is not convertible to requested format
    """
    head_export_check(application_id, str(user.id), format)
    return Response(status_code=204)