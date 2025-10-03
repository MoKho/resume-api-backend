from datetime import datetime
from pydantic import BaseModel, Field
from typing import List, Optional


# Request body for creating a new application
class ApplicationCreate(BaseModel):
    target_job_description: str
    #job_history_ids: List[int]

# Response model for an application
class ApplicationResponse(BaseModel):
    id: int
    user_id: str # Supabase uses UUIDs for user IDs, which are strings
    status: str
    target_job_description: str
    final_resume_text: Optional[str] = None
    created_at: datetime

    class Config:
        orm_mode = True # This allows the model to be created from ORM objects


# --- Resume Check Schemas ---

class ResumeCheckRequest(BaseModel):
    """Request body for checking a resume against a job post.

    Fields:
    - resume_text: full resume as plain text
    - job_post: the job posting / description to compare against
    """
    resume_text: str
    job_post: str

class ResumeCheckResponse(BaseModel):
    """Response model for resume check analysis."""
    analysis: str


# --- Profile Setup Schemas ---

class ResumeUpload(BaseModel):
    resume_text: str




class JobHistoryUpdate(BaseModel):
    id: int
    # Make both fields optional so the user can update one, the other, or both
    detailed_background: Optional[str] = None
    is_default_rewrite: Optional[bool] = None

class JobHistoryResponse(BaseModel):
    id: int
    user_id: str
    company_name: Optional[str] = None
    job_title: Optional[str] = None
    achievements_list: Optional[List[str]] = None
    detailed_background: Optional[str] = None
    is_default_rewrite: Optional[bool] = None  

    class Config:
        orm_mode = True

        
class ProfileResponse(BaseModel):
    id: str
    email: Optional[str] = None
    has_base_resume: bool = False # A helpful field for the frontend

    class Config:
        orm_mode = True