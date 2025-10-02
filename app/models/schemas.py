from datetime import datetime
from pydantic import BaseModel, Field
from typing import List, Optional


# Request body for creating a new application
class ApplicationCreate(BaseModel):
    target_job_description: str
    job_history_ids: List[int]

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

# --- Profile Setup Schemas ---

class ResumeUpload(BaseModel):
    resume_text: str

class JobHistoryUpdate(BaseModel):
    id: int
    detailed_background: str

class JobHistoryResponse(BaseModel):
    id: int
    user_id: str
    company_name: Optional[str] = None
    job_title: Optional[str] = None
    achievements_list: Optional[List[str]] = None
    detailed_background: Optional[str] = None

    class Config:
        orm_mode = True