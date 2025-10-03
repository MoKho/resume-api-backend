# app/routers/profiles.py

from fastapi import APIRouter, Depends, HTTPException
from typing import List
from app.security import get_current_user
from app.models.schemas import ResumeUpload, JobHistoryUpdate, JobHistoryResponse, ProfileResponse
from app.services import llm_service
import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

router = APIRouter(
    prefix="/profiles",
    tags=["profiles"]
)

supabase_url = os.environ.get("SUPABASE_URL")
supabase_service_key = os.environ.get("SUPABASE_SERVICE_KEY")
supabase: Client = create_client(supabase_url, supabase_service_key)

@router.get("/me", response_model=ProfileResponse)
async def get_my_profile(user=Depends(get_current_user)):
    """
    Retrieves the profile for the currently logged-in user.
    """
    user_id = str(user.id)
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
    result = supabase.table("job_histories").select("*").eq("user_id", user_id).order("id").execute().data
    return result


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
