from fastapi import FastAPI
from app.routers import applications, profiles

app = FastAPI(
    title="Resume Tailor API",
    description="An API to tailor resumes for specific job descriptions.",
    version="1.0.0"
)

# Include the routers
app.include_router(applications.router)
app.include_router(profiles.router)


@app.get("/")
def read_root():
    return {"message": "Welcome to the Resume Tailor API"}