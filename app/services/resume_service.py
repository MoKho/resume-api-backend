import os
import logging
import json
import re
from typing import Optional
import datetime
from zoneinfo import ZoneInfo
from supabase import create_client, Client
from dotenv import load_dotenv
from app.services import llm_service
from app.utils import gdrive_resume_utils as gdrive_utils
from app import system_prompts
from app.logging_config import get_logger, bind_logger, configure_logging

configure_logging()
load_dotenv(override=True)

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
    
    # --- Flexible replace helpers ---
    def _whitespace_pattern() -> str:
        # Match any run of whitespace, including non-breaking/zero-width spaces
        return r"[\s\u00A0\u200B]+"

    def _flexible_pattern_from_block(block: str) -> re.Pattern:
        # Build a regex pattern from the block where any contiguous whitespace in the block
        # is matched as a flexible whitespace run in the target.
        parts = []
        i = 0
        while i < len(block):
            ch = block[i]
            if ch.isspace() or ch in ("\u00A0", "\u200B"):
                # Consume the entire whitespace run in the block
                while i < len(block) and (block[i].isspace() or block[i] in ("\u00A0", "\u200B")):
                    i += 1
                parts.append(_whitespace_pattern())
            else:
                parts.append(re.escape(ch))
                i += 1
        pattern = "".join(parts)
        return re.compile(pattern, flags=re.DOTALL | re.MULTILINE)

    def _flexible_replace(haystack: str, needle_block: str, replacement: str) -> tuple[str, bool]:
        """
        Attempt a whitespace-tolerant replacement of the first occurrence of needle_block
        inside haystack. Returns (new_text, replaced_bool) and preserves haystack formatting
        except for the replaced region, which uses `replacement` as-is.
        """
        if not needle_block:
            return haystack, False
        pat = _flexible_pattern_from_block(needle_block)
        m = pat.search(haystack)
        if not m:
            return haystack, False
        start, end = m.span()
        return haystack[:start] + replacement + haystack[end:], True
    try:
        # Step 1: Fetch all necessary data from Supabase
        log.info("Fetching data from Supabase...")
        app_data = supabase.table("applications").select("*").eq("id", application_id).single().execute().data
        profile_data = supabase.table("profiles").select("base_resume_text, base_summary_text, gdrive_master_resume_id, first_name, last_name").eq("id", user_id).single().execute().data
        job_histories_to_rewrite = supabase.table("job_histories").select("*").eq("user_id", user_id).eq("is_default_rewrite", True).execute().data
        
        # Step 2: Analyze the job description
        summarized_jd = llm_service.analyze_job_description(app_data['target_job_description'])

        # Step 3: Rewrite the selected job histories
        rewritten_histories = {}
        for history in job_histories_to_rewrite:
            # Ensure there is detailed background to work with
            #if not history.get('detailed_background'):
            #    log.warning("Skipping rewrite as no detailed background", extra={"history_id": history['id']})
            #    continue

            log.info("Rewriting job history", extra={"history_id": history['job_title']})

            current_resume = history.get('achievements', '') or ""
            #log.info(f'\n\n\n\n\n\n\n\n\nType:  {type(current_resume)} \n and preview of current resume context:{current_resume[:200]}')
            rewritten_text = llm_service.rewrite_job_history(
                job_history_background=history['detailed_background'],
                summarized_job_description=summarized_jd,
                current_resume=current_resume  # Using detailed background as current resume context
            )
            #log.info("Rewritten text for history id %s: %s", history['id'], rewritten_text)
            rewritten_histories[history['id']] = rewritten_text

        # Step 4: Assemble the intermediate resume with real find-and-replace
        updated_resume = profile_data['base_resume_text']
        log.info("Replacing job history sections in the resume...")
        for history in job_histories_to_rewrite:
            history_id = history['id']
            log.info("Processing replacement for history", extra={"history_id": history_id})
            #log.info("original history: %s", history.get('achievements'))
            #log.info("rewritten history: %s", rewritten_histories.get(history_id))
            # Check if this history was successfully rewritten in the previous step
            if history_id not in rewritten_histories:
                log.warning("No rewritten text found for history; skipping replacement", extra={"history_id": history_id})
                continue

            # Get the original block of achievement text.
            original_achievements_block = history.get('achievements')
            if not original_achievements_block:
                log.warning("Skipping replacement as no original achievements block", extra={"history_id": history_id})
                continue
            
            # Get the new, AI-generated text.
            new_rewritten_text = rewritten_histories[history_id]
            #log.info("Original achievements block: %s", original_achievements_block)
            #log.info("New rewritten text: %s", new_rewritten_text)
            #log.info("Replacing original achievements block with rewritten text", extra={"New history": new_rewritten_text})
            # Perform the replacement, but only for the first occurrence to be safe.
            # ...existing code...
            # Before performing replacement:
            if original_achievements_block not in updated_resume:
                log.warning(
                    "Achievements block not found in resume (exact match). "
                    "len_block=%d len_resume=%d",
                    len(original_achievements_block), len(updated_resume)
                )
                # Optional: perform normalized check to confirm newline/whitespace mismatch
                def _norm(s: str) -> str:
                    return (
                        s.replace("\r\n", "\n").replace("\r", "\n")
                        .replace("\u00A0", " ").replace("\u200B", "").replace("\ufeff", "")
                    )
                if _norm(original_achievements_block) in _norm(updated_resume):
                    log.info("Normalized match found (newline/whitespace/punctuation normalization fixes it)")
            # Try flexible replacement that tolerates whitespace differences
            replaced_resume, did_replace = _flexible_replace(updated_resume, original_achievements_block, new_rewritten_text)
            if not did_replace:
                log.warning("Flexible replacement failed for job history block; leaving original text", extra={"history_id": history_id})
            else:
                updated_resume = replaced_resume

        # Step 5: Generate the new summary
        log.info("Generating new professional summary...")
        new_summary = llm_service.generate_professional_summary(updated_resume, summarized_jd)

        # Step 6: Assemble the final resume with conditional logic
        old_summary = profile_data.get('base_summary_text')

        if old_summary:
            log.info("Found existing summary. Replacing it.")
            log.info("Old summary: %s", old_summary)
            log.info("New summary: %s", new_summary)
            # Use flexible replacement for summary as well; if it fails, prepend
            replaced_resume, did_replace = _flexible_replace(updated_resume, old_summary, new_summary)
            if did_replace:
                final_resume = replaced_resume
                log.info("Summary replaced using flexible match")
            else:
                log.warning("Summary replacement failed with flexible match; prepending new summary")
                final_resume = f"{new_summary}\n\n{updated_resume}"
        else:
            logging.info("No existing summary found; ignoring summary replacement")
            #log.info("No existing summary found. Prepending new summary.")
            #final_resume = f"{new_summary}\n\n{updated_resume}"
        
        # Step 7: Build a structured map of the updated fields to support granular UI copy
        updated_fields = {
            "professional_summary": new_summary,
            "work_history": [
                {
                    "id": h["id"],
                    "job_title": h.get("job_title"),
                    "company_name": h.get("company_name"),
                    "text": rewritten_histories.get(h["id"])  # only present if rewritten
                }
                for h in job_histories_to_rewrite
                if h["id"] in rewritten_histories
            ]
        }

                # Final Step: Update the application in the database (always)
        log.info("Updating application in Supabase with final resume and updated fields...")
        # "final_resume_text" stores the fully tailored resume text for the application.
        # Ensure this field exists in your Supabase "applications" table and is documented in your data model.
        update_payload = {
            "final_resume_text": final_resume,
            "updated_fields": updated_fields,
            "status": "completed",
            "updated_at": datetime.datetime.now(ZoneInfo("America/Los_Angeles")).isoformat()
        }
        try:
            response = supabase.table("applications").update(update_payload).eq("id", application_id).execute()
            log.info("Successfully completed tailoring")
            if hasattr(response, "error") and response.error:
                log.error("Failed to update application in Supabase", extra={"error": response.error})
                raise Exception(f"Supabase update failed: {response.error}")
        except Exception as e:
            log.error("Exception occurred while updating application in Supabase", extra={"error": str(e)})
            raise Exception(f"Supabase update failed: {e}")


        # Optional Google Drive branch — if user has a master resume on Drive
        gdrive_pdf_id = None
        try:
            master_id = profile_data.get("gdrive_master_resume_id") if profile_data else None
            if master_id:
                log.info("gdrive_master_resume_id found — creating Drive duplicate and applying changes")

                # Create name for the tailored resume doc
                # Create MonthYear for the name
                month_year = datetime.datetime.now().strftime("%m%y")
                if profile_data.get("first_name") and profile_data.get("last_name"):
                    desired_name = f"Resume-{profile_data['first_name']}{profile_data['last_name']}-{month_year}"
                else:
                    desired_name = f"Resume-{month_year}"
                dup = gdrive_utils.duplicate_master_resume(user_id, desired_name)
                dup_id_val = dup.get("id")
                dup_id: Optional[str] = dup_id_val if isinstance(dup_id_val, str) else None
                if not dup_id:
                    log.warning("Duplicate file created without id; skipping Drive edits")

                # Apply per-history replacements on the Drive doc
                for history in job_histories_to_rewrite:
                    h_id = history.get('id')
                    if not h_id or not dup_id:
                        continue
                    original_achievements_block = history.get('achievements')
                    new_rewritten_text = rewritten_histories.get(h_id)
                    if not original_achievements_block or not new_rewritten_text:
                        continue
                    try:
                        # Prefer whole-block flexible replacement (whitespace tolerant). Fallback to basic replace.
                        res = gdrive_utils.replace_text_block_flexible(dup_id, original_achievements_block, new_rewritten_text)
                        if not res.get("updated"):
                            log.warning("Flexible block replace failed; falling back to basic replace", extra={"history_id": h_id})
                            gdrive_utils.replace_text_in_doc(dup_id, original_achievements_block, new_rewritten_text, replace_all=False)
                    except Exception:
                        log.exception("Drive replace failed for job history", extra={"history_id": h_id})

                # Replace or prepend summary in the Drive doc
                if dup_id:
                    if old_summary:
                        try:
                            res = gdrive_utils.replace_text_block_flexible(dup_id, old_summary, new_summary)
                            if not res.get("updated"):
                                log.warning("Flexible summary replace failed; falling back to basic replace")
                                gdrive_utils.replace_text_in_doc(dup_id, old_summary, new_summary, replace_all=False)
                        except Exception:
                            log.exception("Drive replace failed for summary; attempting prepend")
                            try:
                                gdrive_utils.prepend_text_to_doc_top(dup_id, new_summary)
                            except Exception:
                                log.exception("Drive prepend failed for summary as well")
                    else:
                        logging.info("No existing summary found; ignoring summary replacement")
                    #    try:
                    #        gdrive_utils.prepend_text_to_doc_top(dup_id, new_summary)
                    #    except Exception:
                    #        log.exception("Drive prepend failed for summary")

                # Export to PDF on Drive
                try:
                    if dup_id:
                        gdrive_pdf_id = gdrive_utils.export_doc_to_pdf(dup_id, f"{desired_name}.pdf")
                except Exception:
                    log.exception("Drive export to PDF failed")
            else:
                log.warning("No Drive document ID found; skipping Drive updates")
        except Exception:
            log.exception("Google Drive tailoring branch failed; continuing without Drive artifacts")

        if gdrive_pdf_id:
            # Add gdrive_pdf_id to the application in the database
            log.info("Updating application in Supabase with gdrive_pdf_id...")
            # Ensure this field exists in your Supabase "applications" table and is documented in your data model.
            gdrive_payload = {
                "gdrive_pdf_resume_id": gdrive_pdf_id,
                "updated_at": datetime.datetime.now(ZoneInfo("America/Los_Angeles")).isoformat()
            }
            try:
                response = supabase.table("applications").update(gdrive_payload).eq("id", application_id).execute()
                log.info("Successfully Added gdrive_pdf_id to application")
                err = getattr(response, "error", None)
                if err:
                    log.error("Failed to add gdrive_pdf_id to application in Supabase", extra={"error": err})
                    raise Exception(f"Supabase update failed: {err}")
            except Exception as e:
                log.error("Exception occurred while adding gdrive_pdf_id to application in Supabase", extra={"error": str(e)})
                raise Exception(f"Supabase update failed: {e}")



    except Exception as e:
        log.exception("Error during tailoring process", exc_info=True)
        supabase.table("applications").update({"status": "failed"}).eq("id", application_id).execute()


def run_resume_check_process(user_id: str, job_post: str, resume_text: Optional[str] = None, summarize_job_post: bool = True, qualifications: Optional[str] = None) -> tuple[str, str]:
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
        if qualifications is not None:
            qualifications_text = str(qualifications)
            log.info("Using qualifications text found on resume_checks row", extra={"Qualifications": qualifications_text[:100] })
        elif job_row and job_row.get("qualifications"):
            qualifications_text = job_row.get("qualifications")
            log.info("Using qualifications text found on existing job row", extra={"job id": job_id})
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
            except Exception as e:
                log.exception("Qualifications extraction failed. Failing the job.")
                raise
        # Persist qualifications to the resume_checks row if we found a job_id
        now = datetime.datetime.now(ZoneInfo("America/Los_Angeles")).isoformat()
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
        score = llm_service.score_resume(resume_to_check, qualifications_text or "")
        analysis = llm_service.check_resume(resume_to_check, job_post or "")

        log.info("Analysis complete — returning results")
        return (score, analysis)

    except Exception as e:
        log.exception("Error during resume check process: %s", e)
        raise