import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from app.services.resume_service import supabase, run_tailoring_process
from app.logging_config import get_logger, bind_logger

logger = get_logger(__name__)
POLL_INTERVAL = 0.1  # seconds


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
                app_logger = bind_logger(logger, {"agent_name": "tailoring_worker", "application_id": app_id, "user_id": app.get("user_id")})
                app_logger.info("Picking up application")
                supabase.table("applications").update({"status": "processing", "updated_at": datetime.now(ZoneInfo("America/Los_Angeles")).isoformat()}).eq("id", app_id).execute()
                try:
                    run_tailoring_process(application_id=app_id, user_id=app["user_id"])
                    app_logger.info("Completed application")
                except Exception as e:
                    app_logger.exception("Application processing failed", exc_info=True)
                    supabase.table("applications").update({"status": "failed", "updated_at": datetime.now(ZoneInfo("America/Los_Angeles")).isoformat()}).eq("id", app_id).execute()
            time.sleep(POLL_INTERVAL)
        except Exception as e:
            logger.exception("Tailoring worker loop error: %s", e)
            time.sleep(10)


if __name__ == "__main__":
    process_pending_applications()
