# app/routers/profiles.py


from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from typing import List
from app.security import get_current_user
from app.models.schemas import (
    ResumeUpload,
    JobHistoryUpdate,
    JobHistoryResponse,
    ProfileResponse,
    ResumeCheckResponse,
    ResumeTextResponse,
)
from app.models.schemas import (
    ResumeSummaryResponse,
    ResumeSkillsResponse,
    ResumeFileUploadResponse,
    GoogleDriveFileRef,
    ProcessResumeResponse,
    JobHistoriesResponse,
)
from app.services import llm_service
from app.services.resume_service import run_resume_check_process
from app.models.schemas import ResumeCheckRequest, ResumeCheckEnqueueResponse
from datetime import datetime, timezone
import os
from supabase import create_client, Client
from dotenv import load_dotenv
from app.logging_config import get_logger, bind_logger, configure_logging
from app.services.google_drive_service import (
    build_server_drive_service,
    upload_bytes_as_google_doc,
    upsert_profile_master_resume_id,
    export_google_doc_text,
    export_google_doc_bytes,
)

configure_logging()

load_dotenv(override=True)

router = APIRouter(
    prefix="/profiles",
    tags=["profiles"]
)

logger = get_logger(__name__)
log = bind_logger(logger, {"agent_name": "profiles_router"})
supabase_url = os.environ.get("SUPABASE_URL") or ""
log.info(f"Supabase URL: {supabase_url}")
supabase_service_key = os.environ.get("SUPABASE_SERVICE_KEY") or ""
supabase: Client = create_client(supabase_url, supabase_service_key)


@router.get("/me", response_model=ProfileResponse)
async def get_my_profile(user=Depends(get_current_user)):
    """
    Retrieves the profile for the currently logged-in user.
    """
    user_id = str(user.id)
    log = bind_logger(logger, {"agent_name": "profiles_router", "user_id": user_id})
    result = supabase.table("profiles").select("id, email, base_resume_text").eq("id", user_id).single().execute().data
    
    if not result:
        raise HTTPException(status_code=404, detail="Profile not found.")

    # Create a helpful boolean for the frontend
    result['has_base_resume'] = bool(result.get('base_resume_text'))
    return result

@router.get("/job-histories", response_model=JobHistoriesResponse)
async def get_all_job_histories(user=Depends(get_current_user)):
    """Return all parsed job histories plus stored summary and skills.

    NOTE: Response changed from a raw list -> object with `jobs`, `summary`, `skills`.
    """
    user_id = str(user.id)
    log = bind_logger(logger, {"agent_name": "profiles_router", "user_id": user_id})
    jobs = supabase.table("job_histories").select("*").eq("user_id", user_id).order("id").execute().data
    profile = supabase.table("profiles").select("base_summary_text, base_skills_text").eq("id", user_id).single().execute().data
    summary = profile.get("base_summary_text") if profile else None
    skills = profile.get("base_skills_text") if profile else None
    return {"jobs": jobs or [], "summary": summary, "skills": skills}


@router.get("/resume-text", response_model=ResumeTextResponse)
async def get_my_resume_text(user=Depends(get_current_user)):
    """
    Return the current user's stored base resume text (if any).
    """
    user_id = str(user.id)
    log = bind_logger(logger, {"agent_name": "profiles_router", "user_id": user_id})
    try:
        profile = supabase.table("profiles").select("base_resume_text").eq("id", user_id).single().execute().data
        if not profile:
            log.warning("Profile not found when requesting resume_text")
            raise HTTPException(status_code=404, detail="Profile not found")
        log.info("Returning resume_text")
        return {"resume_text": profile.get("base_resume_text")}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")


@router.get("/summary", response_model=ResumeSummaryResponse)
async def get_my_summary(user=Depends(get_current_user)):
    """
    Return the current user's stored professional summary (base_summary_text).
    """
    user_id = str(user.id)
    log = bind_logger(logger, {"agent_name": "profiles_router", "user_id": user_id})
    try:
        profile = supabase.table("profiles").select("base_summary_text").eq("id", user_id).single().execute().data
        if not profile:
            log.warning("Profile not found when requesting summary")
            raise HTTPException(status_code=404, detail="Profile not found")
        log.info("Returning professional summary")
        return {"summary": profile.get("base_summary_text")}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")


@router.get("/skills", response_model=ResumeSkillsResponse)
async def get_my_skills(user=Depends(get_current_user)):
    """
    Return the current user's stored skills section (base_skills_text).
    """
    user_id = str(user.id)
    log = bind_logger(logger, {"agent_name": "profiles_router", "user_id": user_id})
    try:
        profile = supabase.table("profiles").select("base_skills_text").eq("id", user_id).single().execute().data
        if not profile:
            log.warning("Profile not found when requesting skills")
            raise HTTPException(status_code=404, detail="Profile not found")
        log.info("Returning skills section")
        return {"skills": profile.get("base_skills_text")}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")


@router.post("/process-resume", response_model=ProcessResumeResponse)
async def process_resume(
    resume_data: ResumeUpload,
    user=Depends(get_current_user)
):
    """
    User Story 1:
    Uploads a resume, parses it, and stores the job history.
    This action will DELETE all previous job histories for the user.
    """
    user_id = str(user.id)
    log = bind_logger(logger, {"agent_name": "process-resume", "user_id": user_id})
    try:
        # First, delete any existing job histories to prevent duplicates
        supabase.table("job_histories").delete().eq("user_id", user_id).execute()

        # Call the LLM to parse the resume
        try:
            professional_summary = llm_service.extract_professional_summary(resume_data.resume_text)
            log.info("Extracted professional summary")
            log.info(professional_summary)
        except Exception as e:
            log.error("Error extracting professional summary: %s", str(e))
            raise HTTPException(status_code=500, detail="Error extracting professional summary")

        # Extract skills section
        try:
            skills_text = llm_service.extract_resume_skills(resume_data.resume_text)
            log.info("Extracted skills section")
            log.info(skills_text)
        except Exception as e:
            log.error("Error extracting skills: %s", str(e))
            raise HTTPException(status_code=500, detail="Error extracting skills section")

        try:
            parsed_jobs = llm_service.parse_resume_to_json(resume_data.resume_text)
        except Exception as e:
            log.error("Error extracting job histories from resume: %s", str(e))
            raise HTTPException(status_code=500, detail="Error extracting job histories from resume")

        # --- THIS IS THE NEW MAPPING LOGIC ---
        # Transform the LLM output to match our database schema.
        jobs_to_insert = []
        for job in parsed_jobs:
            jobs_to_insert.append({
                'user_id': user_id,
                'company_name': job.get('history_company_name'),
                'job_title': job.get('history_job_title'),
                # Store the entire original achievements/responsibilities block as a single string
                'achievements': job.get('history_job_achievements') or ""
            })

        # Bulk insert the new, correctly formatted job histories
        inserted_data = supabase.table("job_histories").insert(jobs_to_insert).execute().data

        # Merge profile updates into a single payload and perform one update call
        profile_update = {}
        if professional_summary is not None:
            profile_update["base_summary_text"] = professional_summary
        if skills_text is not None:
            profile_update["base_skills_text"] = skills_text
        if resume_data.resume_text is not None:
            profile_update["base_resume_text"] = resume_data.resume_text

        if profile_update:
            supabase.table("profiles").update(profile_update).eq("id", user_id).execute()

        log.info("Inserted parsed job histories", extra={"inserted_count": len(inserted_data) if inserted_data else 0})
        return {
            "jobs": inserted_data or [],
            "summary": professional_summary,
            "skills": skills_text,
        }
    except ValueError as e:
        log.error("ValueError during resume processing: %s", str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error("Unexpected error during resume processing: %s", str(e))
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")
    
    
@router.patch("/job-histories", response_model=List[JobHistoryResponse])
async def update_job_histories(
    updates: List[JobHistoryUpdate],
    user=Depends(get_current_user)
):
    """
    Updates the detailed_background and/or the is_default_rewrite flag
    for one or more job histories.
    """
    user_id = str(user.id)
    valid_ids_response = supabase.table("job_histories").select("id").eq("user_id", user_id).execute().data
    valid_ids = {item['id'] for item in valid_ids_response}

    updated_records = []
    
    for update in updates:
        if update.id not in valid_ids:
            raise HTTPException(status_code=403, detail=f"Not authorized to update job history with id {update.id}")
        
        update_payload = {}
        if update.detailed_background is not None:
            update_payload['detailed_background'] = update.detailed_background
        if update.is_default_rewrite is not None:
            update_payload['is_default_rewrite'] = update.is_default_rewrite

        # Only perform an update if there's something to change
        if update_payload:
            result = supabase.table("job_histories").update(update_payload).eq("id", update.id).execute().data
            if result:
                updated_records.append(result[0])
            
    return updated_records


@router.post("/upload-resume", response_model=ResumeFileUploadResponse)
async def upload_resume_file(file: UploadFile = File(...), user=Depends(get_current_user)):
    """Upload a resume file and follow the same flow as open-file:

    - Validate extension (pdf, docx, doc, txt, md)
    - Upload bytes to the server's Google Drive converting to a Google Doc
    - Update `profiles.gdrive_master_resume_id`
    - Export and return plain text + markdown
    """
    user_id = str(user.id)
    log = bind_logger(logger, {"agent_name": "upload-resume", "user_id": user_id})

    # Validate extension and determine source mime
    filename = file.filename or "uploaded"
    name_lower = filename.lower()
    ext = name_lower.rsplit(".", 1)[-1] if "." in name_lower else ""
    allowed = {"pdf", "docx", "doc", "txt", "md"}
    if ext not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported file type. Allowed: pdf, docx, doc, txt, md")

    ext_to_mime = {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "doc": "application/msword",
        "txt": "text/plain",
        "md": "text/markdown",
    }
    source_mime = ext_to_mime.get(ext, "application/octet-stream")

    try:
        raw_bytes = await file.read()
        if not raw_bytes:
            raise HTTPException(status_code=400, detail="Empty file upload")

        server_drive = build_server_drive_service()
        dest_name = f"{user_id}-master-resume"

        # Always convert to a Google Doc for the master resume copy
        log.info("upload-resume: uploading bytes as Google Doc", extra={"dest_name": dest_name, "source_mime": source_mime})
        dest_file = upload_bytes_as_google_doc(server_drive, raw_bytes, source_mime, dest_name)
        dest_file_id = dest_file.get("id")
        if not dest_file_id:
            raise HTTPException(status_code=500, detail="Failed to create Google Doc for uploaded file")

        # Update profile with master resume file id
        upsert_profile_master_resume_id(user_id, dest_file_id)
        log.info("upload-resume: profile updated with gdrive_master_resume_id", extra={"dest_file_id": dest_file_id})

        # Export text and markdown
        content = export_google_doc_text(server_drive, dest_file_id)
        content_md = ""
        try:
            md_bytes = export_google_doc_bytes(server_drive, dest_file_id, "text/markdown")
            content_md = md_bytes.decode("utf-8", errors="ignore")
        except Exception as e:
            log.warning("upload-resume: markdown export failed", extra={"error": str(e)})

        response = ResumeFileUploadResponse(
            destination=GoogleDriveFileRef(
                fileId=dest_file_id,
                mimeType=dest_file.get("mimeType"),
                name=dest_file.get("name"),
            ),
            content=content,
            content_md=content_md,
        )
        log.info("upload-resume: done", extra={"dest_file_id": dest_file_id})
        return response
    except HTTPException:
        raise
    except Exception as e:
        log.error("upload-resume: unexpected error", extra={"error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to upload and process file: {e}")



@router.post("/check-resume", status_code=202, response_model=ResumeCheckEnqueueResponse)
async def enqueue_resume_check(request: ResumeCheckRequest, user=Depends(get_current_user)):
    """Enqueue a resume check job and return job id + status URL."""
    user_id = str(user.id)
    log = bind_logger(logger, {"agent_name": "profiles_router", "user_id": user_id})
    try:
        now = datetime.now(ZoneInfo("America/Los_Angeles")).isoformat()

        if request.job_post is None or request.job_post.strip() == "":
            if request.qualifications is None or request.qualifications.strip() == "":
                raise HTTPException(status_code=400, detail="Either job_post or qualifications must be provided and non-empty.")
        payload = {
            "user_id": user_id,
            "job_post": request.job_post,
            "resume_text": request.resume_text or None,
            # Allow the frontend to supply a pre-extracted qualifications list to
            # skip summarization/extraction. This should be a list of objects
            # {"qualification": str, "weight": int} and will be stored in the
            # resume_checks.qualifications JSONB column.
            "qualifications": request.qualifications if getattr(request, "qualifications", None) is not None else None,
            "status": "pending",
            "analysis": None,
            "error": None,
            "summarize_job_post": request.summarize_job_post if getattr(request, "summarize_job_post", None) is not None else True,
            "created_at": now,
            "updated_at": now
        }
        result = supabase.table("resume_checks").insert(payload).execute()
        err = getattr(result, "error", None)
        if err:
            log.error("Failed to enqueue resume check: %s", getattr(err, "message", str(err)))
            raise HTTPException(status_code=500, detail="Failed to enqueue job")
        job_row = result.data[0]
        job_id = job_row["id"]
        log.info("Enqueued resume_check job", extra={"job_id": job_id})
        return {"job_id": job_id, "status_url": f"/profiles/check-resume/{job_id}", "status": "pending"}
    except HTTPException as e:
        log.error("HTTP error occurred: %s", e.detail)
        raise
    except Exception as e:
        log.exception("Error enqueuing resume check: %s", e)
        raise HTTPException(status_code=500, detail=f'Internal error: {e}')


@router.get("/check-resume/{job_id}", response_model=ResumeCheckResponse)
async def get_resume_check_status(job_id: int, user=Depends(get_current_user)):
    """
    Retrieve the status and analysis of an enqueued resume check job.
    """
    user_id = str(user.id)
    try:
        row = supabase.table("resume_checks").select("*").eq("id", job_id).single().execute().data
        if not row:
            raise HTTPException(status_code=404, detail="Job not found")
        if row.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Not authorized to view this job")

        return {
            "job_id": row.get("id"),
            "status": row.get("status"),
            "analysis": row.get("analysis"),
            "score": row.get("score"),
            "raw_score_csv": row.get("raw_score_csv"),
            "error": row.get("error"),
            "qualifications": row.get("qualifications"),
            "updated_at": row.get("updated_at")
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")