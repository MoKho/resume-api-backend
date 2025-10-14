# app/routers/profiles.py

from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from typing import List
from app.security import get_current_user
from app.models.schemas import ResumeUpload, JobHistoryUpdate, JobHistoryResponse, ProfileResponse, ResumeCheckResponse, ResumeTextResponse
from app.models.schemas import ResumeSummaryResponse
from app.services import llm_service
from app.services.resume_service import run_resume_check_process
from app.models.schemas import ResumeCheckRequest, ResumeCheckEnqueueResponse
from datetime import datetime, timezone
import os
from supabase import create_client, Client
from dotenv import load_dotenv
from app.logging_config import get_logger, bind_logger, configure_logging

configure_logging()

load_dotenv()

router = APIRouter(
    prefix="/profiles",
    tags=["profiles"]
)

supabase_url = os.environ.get("SUPABASE_URL") or ""
supabase_service_key = os.environ.get("SUPABASE_SERVICE_KEY") or ""
supabase: Client = create_client(supabase_url, supabase_service_key)
logger = get_logger(__name__)

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

@router.get("/job-histories", response_model=List[JobHistoryResponse])
async def get_all_job_histories(user=Depends(get_current_user)):
    """
    Retrieves a list of all parsed job histories for the logged-in user.
    """
    user_id = str(user.id)
    log = bind_logger(logger, {"agent_name": "profiles_router", "user_id": user_id})
    result = supabase.table("job_histories").select("*").eq("user_id", user_id).order("id").execute().data
    return result


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


@router.post("/process-resume", response_model=List[JobHistoryResponse])
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
    log = bind_logger(logger, {"agent_name": "profiles_router", "user_id": user_id})
    try:
        # First, delete any existing job histories to prevent duplicates
        supabase.table("job_histories").delete().eq("user_id", user_id).execute()

        # Call the LLM to parse the resume
        parsed_jobs = llm_service.parse_resume_to_json(resume_data.resume_text)

        # --- THIS IS THE NEW MAPPING LOGIC ---
        # Transform the LLM output to match our database schema.
        jobs_to_insert = []
        for job in parsed_jobs:
            jobs_to_insert.append({
                'user_id': user_id,
                'company_name': job.get('history_company_name'),
                'job_title': job.get('history_job_title'),
                'achievements_list': job.get('history_job_achievements', [])
            })

        # Bulk insert the new, correctly formatted job histories
        inserted_data = supabase.table("job_histories").insert(jobs_to_insert).execute().data

        # Also, update the base_resume_text in the user's profile
        supabase.table("profiles").update({"base_resume_text": resume_data.resume_text}).eq("id", user_id).execute()

        log.info("Inserted parsed job histories", extra={"inserted_count": len(inserted_data) if inserted_data else 0})
        return inserted_data
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
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



@router.post("/check-resume", status_code=202, response_model=ResumeCheckEnqueueResponse)
async def enqueue_resume_check(request: ResumeCheckRequest, user=Depends(get_current_user)):
    """Enqueue a resume check job and return job id + status URL."""
    user_id = str(user.id)
    log = bind_logger(logger, {"agent_name": "profiles_router", "user_id": user_id})
    try:
        now = datetime.now(ZoneInfo("America/Los_Angeles"))
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
    except HTTPException:
        raise
    except Exception:
        log.exception("Error enqueuing resume check")
        raise HTTPException(status_code=500, detail="Internal error")


@router.get("/check-resume/{job_id}")
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
            "error": row.get("error"),
            "qualifications": row.get("qualifications"),
            "updated_at": row.get("updated_at")
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")