from fastapi import APIRouter, Depends, HTTPException
from app.security import get_current_user
from app.models.schemas import ApplicationCreate, ApplicationResponse
from app.services.resume_service import run_tailoring_process
import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

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