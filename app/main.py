from fastapi import FastAPI
from app.routers import applications, profiles
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Resume Tailor API",
    description="An API to tailor resumes for specific job descriptions.",
    version="1.0.0"
)

# This list defines which origins are allowed to communicate with your backend.
origins = [
    "http://localhost",       # For local development
    "http://localhost:3000",  # Default for Next.js dev server
    "https://aistudio.google.com/apps/drive/1OMMs_Qxb1qI24GqapxALM1nrKwNTgywj", # Google AI Studio
    "http://localhost:3000",  # For local Next.js development
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
    return {"message": "Welcome to the Resume Tailor API"}