import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from app.services.resume_service import supabase, run_resume_check_process
from app.logging_config import get_logger, bind_logger, configure_logging

configure_logging()
logger = get_logger(__name__)
POLL_INTERVAL = 5  # seconds


def process_pending_jobs():
    """Simple poller that picks up pending resume_checks jobs and processes them.

    This is intentionally simple and file-based. For production consider using
    a real queue (Redis + RQ, Celery, or a managed queue service) to get retries
    and better concurrency control.
    """
    while True:
        try:
            rows = supabase.table("resume_checks").select("*").eq("status", "pending").limit(5).execute().data or []
            for job in rows:
                job_id = job["id"]
                job_logger = bind_logger(logger, {"agent_name": "resume_check_worker", "job_id": job_id, "user_id": job.get("user_id")})
                job_logger.info("Picking up resume_check job")
                supabase.table("resume_checks").update({"status": "processing", "updated_at": datetime.now(ZoneInfo("America/Los_Angeles")).isoformat()}).eq("id", job_id).execute()
                try:
                    summarize_flag = job.get("summarize_job_post", True)
                    analysis = run_resume_check_process(
                        user_id=job["user_id"],
                        job_post=job["job_post"],
                        resume_text=job.get("resume_text"),
                        summarize_job_post=summarize_flag,
                        qualifications=job.get("qualifications")
                    )
                    supabase.table("resume_checks").update({
                        "status": "completed",
                        "analysis": analysis,
                        "updated_at": datetime.now(ZoneInfo("America/Los_Angeles")).isoformat()
                    }).eq("id", job_id).execute()
                    job_logger.info("Completed resume_check job")
                except Exception as e:
                    job_logger.exception("Job failed", exc_info=True)
                    supabase.table("resume_checks").update({
                        "status": "failed",
                        "error": str(e)[:2000],
                        "updated_at": datetime.now(ZoneInfo("America/Los_Angeles")).isoformat()
                    }).eq("id", job_id).execute()
            time.sleep(POLL_INTERVAL)
        except Exception as e:
            logger.exception("Worker loop error: %s", e)
            time.sleep(10)


if __name__ == "__main__":
    process_pending_jobs()
