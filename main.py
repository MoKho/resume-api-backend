import os
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from supabase import create_client, Client
from pydantic import BaseModel
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Initialize FastAPI app
app = FastAPI()

# Initialize Supabase client
supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_ANON_KEY")
supabase: Client = create_client(supabase_url, supabase_key)

# Pydantic model for the request body
class HelloWorldRequest(BaseModel):
    name: str

# Security scheme for JWT authentication
security = HTTPBearer()

# Dependency to get the current user from the JWT token
async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        token = credentials.credentials
        user_response = supabase.auth.get_user(token)
        user = user_response.user
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")
        return user
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")

@app.post("/helloworld")
async def helloworld(request: HelloWorldRequest, user = Depends(get_current_user)):
    return {"message": f"Hello {request.name}, you are an authenticated user with email: {user.email}"}

@app.get("/")
def read_root():
    return {"message": "Welcome to the HelloWorld API. Use the /helloworld endpoint with a POST request and a valid JWT token."}