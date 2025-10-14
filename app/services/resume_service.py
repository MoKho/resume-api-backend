import os
import logging
import json
from typing import Optional
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from supabase import create_client, Client
from dotenv import load_dotenv
from app.services import llm_service
from app import system_prompts
from app.logging_config import get_logger, bind_logger, configure_logging

configure_logging()
load_dotenv()

logger = get_logger(__name__)

# We need to initialize another Supabase client here.
# IMPORTANT: For backend services, we use the SERVICE_ROLE_KEY
# which has full access to the database, bypassing any Row Level Security.
# Be very careful with this key.
supabase_url = os.environ.get("SUPABASE_URL")
supabase_service_key = os.environ.get("SUPABASE_SERVICE_KEY") # <-- NOTICE THE DIFFERENT KEY

# Ensure environment variables are present (narrow types for static checkers)
if not supabase_url or not supabase_service_key:
    logger.error("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY environment variables. Supabase client cannot be initialized.")
    raise EnvironmentError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in environment")

supabase: Client = create_client(supabase_url, supabase_service_key)

def run_tailoring_process(application_id: int, user_id: str):
    log = bind_logger(logger, {"agent_name": "tailoring_worker", "user_id": user_id, "application_id": application_id})
    log.info("Starting tailoring process")
    try:
        # Step 1: Fetch all necessary data from Supabase
        log.info("Fetching data from Supabase...")
        app_data = supabase.table("applications").select("*").eq("id", application_id).single().execute().data
        profile_data = supabase.table("profiles").select("base_resume_text, base_summary_text").eq("id", user_id).single().execute().data
        job_histories_to_rewrite = supabase.table("job_histories").select("*").eq("is_default_rewrite", True).execute().data
        
        # Step 2: Analyze the job description
        summarized_jd = llm_service.analyze_job_description(app_data['target_job_description'])

        # Step 3: Rewrite the selected job histories
        rewritten_histories = {}
        for history in job_histories_to_rewrite:
            # Ensure there is detailed background to work with
            if not history.get('detailed_background'):
                log.warning("Skipping rewrite as no detailed background", extra={"history_id": history['id']})
                continue

            rewritten_text = llm_service.rewrite_job_history(
                job_history_background=history['detailed_background'],
                summarized_job_description=summarized_jd
            )
            rewritten_histories[history['id']] = rewritten_text

        # Step 4: Assemble the intermediate resume with real find-and-replace
        updated_resume = profile_data['base_resume_text']

        log.info("Replacing job history sections in the resume...")
        for history in job_histories_to_rewrite:
            history_id = history['id']
            # Check if this history was successfully rewritten in the previous step
            if history_id not in rewritten_histories:
                continue

            # Reconstruct the original block of achievement text from the stored list.
            # This relies on the parsing LLM splitting achievements by newlines.
            original_achievements = history.get('achievements_list')
            if not original_achievements:
                log.warning("Skipping replacement as no original achievements list", extra={"history_id": history_id})
                continue
            
            original_achievements_block = "\n".join(original_achievements)
            
            # Get the new, AI-generated text.
            new_rewritten_text = rewritten_histories[history_id]

            # Perform the replacement, but only for the first occurrence to be safe.
            updated_resume = updated_resume.replace(original_achievements_block, new_rewritten_text, 1)

        # Step 5: Generate the new summary
        log.info("Generating new professional summary...")
        new_summary = llm_service.generate_professional_summary(updated_resume, summarized_jd)

        # Step 6: Assemble the final resume with conditional logic
        old_summary = profile_data.get('base_summary_text')

        if old_summary:
            log.info("Found existing summary. Replacing it.")
            final_resume = updated_resume.replace(old_summary, new_summary)
        else:
            log.info("No existing summary found. Prepending new summary.")
            final_resume = f"{new_summary}\n\n{updated_resume}"
        
        # Final Step: Update the application in the database
        log.info("Updating application in Supabase with final resume...")
        supabase.table("applications").update({
            "final_resume_text": final_resume,
            "status": "completed"
        }).eq("id", application_id).execute()
        log.info("Successfully completed tailoring")

    except Exception as e:
        log.exception("Error during tailoring process", exc_info=True)
        supabase.table("applications").update({"status": "failed"}).eq("id", application_id).execute()


def run_resume_check_process(user_id: str, job_post: str, resume_text: Optional[str] = None, summarize_job_post: bool = True) -> str:
    """
    Run a resume vs job-post analysis and return a detailed textual analysis.

    Args:
        user_id (str): The id of the user whose generic resume may be fetched if resume_text is not provided.
        job_post (str): The job posting text to analyze against (required).
        resume_text (str, optional): The resume text to analyze. If omitted, the user's `base_resume_text` will be fetched from Supabase.

    Returns:
        str: A large text containing the analysis and comparison between the resume and the job post.

    Behavior:
        - If resume_text is None, fetch the user's generic resume from the `profiles` table.
        - Use the LLM agent `resume-match-agent` and the system prompt `resume_match_analyzer_agent_system_prompt` from `app.system_prompts`.
        - Use `llm_service.call_llm_provider` to make the provider call.
        - Logs status and errors and returns the analysis string on success. Raises on fatal errors.
    """

    # Bind a logger adapter for this operation so logs include user context
    log = bind_logger(logger, {"agent_name": "resume_check_process", "user_id": user_id})
    log.info("Starting resume check process")
    try:
        # Attempt to find the corresponding resume_checks row so we can persist qualifications
        try:
            # Keep the query simple and avoid provider-specific order parameters
            job_rows = supabase.table("resume_checks").select("*").eq("user_id", user_id).eq("job_post", job_post).limit(1).execute().data
            job_row = (job_rows[0] if job_rows else None)
            job_id = job_row.get("id") if job_row else None
        except Exception as e:
            log.error(f"Failed to retrieve job row: {e}")
            # Fallback: don't fail the whole job if we can't read the row
            job_row = None
            job_id = None

        # Step 1: Ensure we have a resume to analyze. If not provided, fetch the user's generic resume.
        if not resume_text:
            log.info("No resume provided — fetching base resume for user from Supabase")
            profile_data = supabase.table("profiles").select("base_resume_text, base_summary_text").eq("id", user_id).single().execute().data
            if not profile_data or not profile_data.get('base_resume_text'):
                log.error("No base resume found for user; aborting analysis")
                raise ValueError("No resume available for analysis")
            resume_text = profile_data['base_resume_text']
            log.debug("Fetched base resume from Supabase for user_id: %s", user_id)

        # Step 2: Determine or extract the qualifications text (string)
        qualifications_text: Optional[str] = None
        # Prefer qualifications already present on the resume_checks row
        if job_row and job_row.get("qualifications"):
            qualifications_text = str(job_row.get("qualifications"))
            log.info("Using qualifications text found on resume_checks row", extra={"length": len(qualifications_text)})
        else:
            # If not present, derive it. If summarize_job_post is True, summarize first then extract.
            try:
                if summarize_job_post:
                    log.info("Summarizing job posting before extracting qualifications")
                    summarized_jd = llm_service.analyze_job_description(job_post)
                    log.debug("Summarized job description preview: %s", summarized_jd[:200])
                    # extractor returns a list[dict]; convert to string so LLM can use it even if it's not perfect
                    qualifications_text = llm_service.extract_job_qualifications(summarized_jd)

                else:
                    log.info("Extracting qualifications directly from provided job post")
                    qualifications_text = llm_service.extract_job_qualifications(job_post)

            except Exception:
                # Fallback: if extraction fails entirely, use the (summarized) job post as a proxy
                log.exception("Qualifications extraction failed; falling back to raw job post text")
                qualifications_text = job_post

        # Persist qualifications to the resume_checks row if we found a job_id
        now = datetime.now(ZoneInfo("America/Los_Angeles"))
        if job_id and qualifications_text is not None:
            try:
                supabase.table("resume_checks").update({"qualifications": qualifications_text, "updated_at": now}).eq("id", job_id).execute()
                log.info("Saved qualifications text to resume_checks row", extra={"job_id": job_id, "length": len(qualifications_text)})
            except Exception:
                log.exception("Failed to save qualifications to resume_checks row; continuing without persisting")

        log.info("Requesting resume vs qualifications analysis from LLM service")
        # Ensure type-checkers know this is a str (we validated above)
        resume_to_check: str = resume_text  # type: ignore[assignment]
        assert isinstance(resume_to_check, str)
        # Pass the qualifications text directly to the LLM for scoring (LLMs can parse strings)
        analysis = llm_service.check_resume(resume_to_check, qualifications_text or "")

        log.info("Analysis complete — returning results")
        return analysis

    except Exception as e:
        log.exception("Error during resume check process: %s", e)
        raise