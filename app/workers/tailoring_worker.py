import time
import logging
from datetime import datetime, timezone
from app.services.resume_service import supabase, run_tailoring_process

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)
POLL_INTERVAL = 5  # seconds


def process_pending_applications():
    """Simple poller that picks up pending applications and runs tailoring.

    This mirrors the resume_check worker but calls run_tailoring_process which
    updates the application row when complete.
    """
    while True:
        try:
            rows = supabase.table("applications").select("*").eq("status", "pending").limit(5).execute().data or []
            for app in rows:
                app_id = app["id"]
                logger.info("Picking up application id=%s", app_id)
                supabase.table("applications").update({"status": "processing", "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", app_id).execute()
                try:
                    run_tailoring_process(application_id=app_id, user_id=app["user_id"])
                    logger.info("Completed application id=%s", app_id)
                except Exception as e:
                    logger.exception("Application processing failed id=%s: %s", app_id, e)
                    supabase.table("applications").update({"status": "failed", "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", app_id).execute()
            time.sleep(POLL_INTERVAL)
        except Exception as e:
            logger.exception("Tailoring worker loop error: %s", e)
            time.sleep(10)


if __name__ == "__main__":
    process_pending_applications()
