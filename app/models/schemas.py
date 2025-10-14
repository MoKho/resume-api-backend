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
    resume_text: Optional[str] = None
    job_post: str
    # If True (default), the server will run the job-description summarizer
    # before calling the resume-match agent. Set to False if the job_post is
    # already a condensed/summarized form to avoid extra LLM calls.
    summarize_job_post: Optional[bool] = True
    # Optional pre-extracted qualifications text. Provide as a raw string
    # (e.g., a JSON-like array or any structured text). LLMs can parse it.
    qualifications: Optional[str] = None

class ResumeCheckEnqueueResponse(BaseModel):
    job_id: int
    status_url: str
    status: str

class ResumeCheckResponse(BaseModel):
    """Response model for resume check analysis.

    Fields:
    - job_id: The job ID.
    - status: The status of the job.
    - analysis: The analysis result as a string.
    - score: An integer score between 0 and 100 (inclusive).
    - raw_score_csv: The raw CSV string returned by the scoring agent.
    - error: Any error message.
    - qualifications: Qualifications text.
    - updated_at: Timestamp of last update.
    """
    job_id: int
    status: str
    analysis: Optional[str] = None
    score: Optional[int] = Field(None, ge=0, le=100)  # Score between 0 and 100
    raw_score_csv: Optional[str] = None
    error: Optional[str] = None
    qualifications: Optional[str] = None
    updated_at: Optional[str] = None


class ResumeTextResponse(BaseModel):
    """Return the stored resume text for a user."""
    resume_text: Optional[str] = None


class ResumeSummaryResponse(BaseModel):
    """Return the stored professional summary for a user."""
    summary: Optional[str] = None


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


