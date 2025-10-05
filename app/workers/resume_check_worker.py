import time
import logging
from datetime import datetime, timezone
from app.services.resume_service import supabase, run_resume_check_process

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)
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
                logger.info("Picking up resume_check job_id=%s", job_id)
                supabase.table("resume_checks").update({"status": "processing", "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", job_id).execute()
                try:
                    analysis = run_resume_check_process(user_id=job["user_id"], job_post=job["job_post"], resume_text=job.get("resume_text"))
                    supabase.table("resume_checks").update({
                        "status": "completed",
                        "analysis": analysis,
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }).eq("id", job_id).execute()
                    logger.info("Completed resume_check job_id=%s", job_id)
                except Exception as e:
                    logger.exception("Job failed job_id=%s: %s", job_id, e)
                    supabase.table("resume_checks").update({
                        "status": "failed",
                        "error": str(e)[:2000],
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }).eq("id", job_id).execute()
            time.sleep(POLL_INTERVAL)
        except Exception as e:
            logger.exception("Worker loop error: %s", e)
            time.sleep(10)


if __name__ == "__main__":
    process_pending_jobs()
