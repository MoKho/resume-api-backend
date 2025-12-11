from datetime import datetime
from pydantic import BaseModel, Field
from pydantic import ConfigDict
from typing import List, Optional, Dict, Any


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
    # ID of the Google Doc source for the tailored resume (Drive file ID)
    gdrive_doc_resume_id: Optional[str] = None
    # Consolidated JSON of the specific resume sections the workflow updated.
    # Example shape:
    # {
    #   "professional_summary": "...",
    #   "work_history": [
    #       {"id": 123, "job_title": "...", "company_name": "...", "text": "..."}
    #   ]
    # }
    updated_fields: Optional[Dict[str, Any]] = None
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
    # If False, only run the numeric scoring step (faster). If True, run full analysis.
    run_analysis: Optional[bool] = True
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


class ResumeSkillsResponse(BaseModel):
    """Return the stored skills section for a user."""
    skills: Optional[str] = None


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
    # Entire achievements/responsibilities block as a single string exactly as
    # it appears in the resume (line breaks preserved).
    achievements: Optional[str] = None
    detailed_background: Optional[str] = None
    is_default_rewrite: Optional[bool] = None  

    class Config:
        orm_mode = True

class ProcessResumeResponse(BaseModel):
    """Response for the `/process-resume` endpoint.

    Fields:
    - jobs: list of parsed job history records inserted for the user
    - summary: extracted professional summary text (optional)
    - skills: extracted skills section text (optional)
    """
    jobs: List[JobHistoryResponse]
    summary: Optional[str] = None
    skills: Optional[str] = None

    class Config:
        orm_mode = True

class JobHistoriesResponse(BaseModel):
    """Combined response for job histories plus stored summary and skills.

    Returned by GET /profiles/job-histories.
    """
    jobs: List[JobHistoryResponse]
    summary: Optional[str] = None
    skills: Optional[str] = None

    class Config:
        orm_mode = True
        
class ProfileResponse(BaseModel):
    id: str
    email: Optional[str] = None
    has_base_resume: bool = False # A helpful field for the frontend

    class Config:
        orm_mode = True


# --- Google Drive Open-File Schemas ---

class GoogleDriveFileRef(BaseModel):
    """Represents a minimal Drive file reference."""
    fileId: str
    mimeType: Optional[str] = None
    name: Optional[str] = None


class GoogleDriveOpenFileRequest(BaseModel):
    """Request to open a file from user's Drive and copy it to server's Drive."""
    fileId: str


class GoogleDriveOpenFileResponse(BaseModel):
    """Response containing source/destination refs and extracted content."""
    source: GoogleDriveFileRef
    destination: GoogleDriveFileRef
    content: str
    content_md: str


class ResumeFileUploadResponse(BaseModel):
    """Response for uploading a resume file directly to the server.

    Mirrors the output shape of Google Drive open-file but without a source ref.
    """
    destination: GoogleDriveFileRef
    content: str
    content_md: str


# --- Structured Output Schemas for LLM Extraction ---

class ResumeHistoryItem(BaseModel):
    """Single resume history entry extracted by the LLM.

    Fields mirror the expected output of the resume history extractor agent.
    """
    history_job_title: str
    history_company_name: str
    # Entire achievements/responsibilities block as a single string
    history_job_achievements: str

    # Ensure additionalProperties: false in generated JSON Schema
    model_config = ConfigDict(extra='forbid')


class ResumeHistoryExtraction(BaseModel):
    """Top-level schema for structured extraction of resume history.

    The extractor agent should return an object with a `jobs` array of
    ResumeHistoryItem. We forbid additional properties to keep schema strict.
    """
    jobs: List[ResumeHistoryItem]

    # Ensure additionalProperties: false in generated JSON Schema
    model_config = ConfigDict(extra='forbid')


