from fastapi import FastAPI
from app.routers import applications, profiles
from fastapi.middleware.cors import CORSMiddleware
from app.logging_config import configure_logging, get_logger, bind_logger
import os
import errno
import pwd
import grp
configure_logging()

# Configure structured JSON logging early. Attempt to write to a rotating file
# under /var/log/resume_api and fall back to console-only logging if that
# directory cannot be created or is not writable by this process.
LOG_DIR = "/var/log/resume_api"
LOG_FILE = os.path.join(LOG_DIR, "resume_api.log")

def _ensure_log_dir(path: str) -> bool:
    """Create log dir, optionally chown to LOG_DIR_OWNER, and verify writability.

    Returns True if the directory exists and is writable by the current process.
    """
    try:
        os.makedirs(path, exist_ok=True)

        # If an owner is specified, attempt to chown the directory.
        owner = os.environ.get("LOG_DIR_OWNER")
        if owner:
            try:
                uid = pwd.getpwnam(owner).pw_uid
                gid = grp.getgrnam(owner).gr_gid
                os.chown(path, uid, gid)
            except KeyError:
                # Specified owner does not exist on this host; skip chown.
                pass

        # Make the directory readable/executable by others but writable only by owner
        try:
            os.chmod(path, 0o755)
        except Exception:
            # chmod failures aren't fatal for our purposes
            pass

        # Verify writability by attempting to open a temp file for append
        test_file = os.path.join(path, ".writetest")
        try:
            with open(test_file, "a") as f:
                f.write("")
            try:
                os.remove(test_file)
            except Exception:
                pass
            return True
        except PermissionError:
            return False
        except OSError:
            return False

    except PermissionError:
        return False
    except OSError as e:
        if e.errno == errno.EACCES:
            return False
        raise


# Try to enable file logging. If that fails, configure console-only logging.
if _ensure_log_dir(LOG_DIR):
    configure_logging(log_file=LOG_FILE)
else:
    # Fallback: still configure logging but without file output
    configure_logging()

logger = get_logger(__name__)

app = FastAPI(
    title="Resume Tailor API",
    description="An API to tailor resumes for specific job descriptions.",
    version="1.0.0"
)

# This list defines which origins are allowed to communicate with your backend.
origins = [
    "https://resume.p-q.app/",
    # The URL of API itself (good for testing)
    "https://api.p-q.app",
    #frontend URL
    "https://resume.p-q.app/",
    "https://ai-resume-tailor-242175857987.us-west1.run.app"
    "http://localhost",       # For local development
    "http://localhost:3000",  # Default for Next.js dev server
    "http://localhost:8000",
    "https://aistudio.google.com/apps/drive/1OMMs_Qxb1qI24GqapxALM1nrKwNTgywj", # Google AI Studio
    "https://2ln1bx7hzfopwqw021gl6ih56sbcopkebkobr14kqimqvyawx7-h813239537.scf.usercontent.goog", # Your AI Studio URL
    "*" # A wildcard to allow any origin for now
    # Add your future frontend production URL here, e.g., "https://www.your-frontend-app.com"
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # Allows specific origins
    allow_credentials=True, # Allows cookies/authorization headers
    allow_methods=["*"],    # Allows all methods (GET, POST, etc.)
    allow_headers=["*"],    # Allows all headers
)


# Include the routers
app.include_router(applications.router)
app.include_router(profiles.router)



@app.get("/")
def read_root():
    log = bind_logger(logger, {"agent_name": "http-root", "step": "health_check"})
    log.info("root endpoint hit")
    return {"message": "Welcome to the Resume Tailor API"}