import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.routers import twilio_voice, simulator_api, outbound_call

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="AI Voice Interview Practice Agent",
    version="1.0.0"
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Routers
app.include_router(twilio_voice.router)
app.include_router(simulator_api.router)
app.include_router(outbound_call.router)

# Mount Static directory
static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/health", tags=["System"])
async def health_check():
    return {
        "status": "ok",
        "app": settings.PROJECT_NAME,
        "groq_configured": bool(settings.GROQ_API_KEY and settings.GROQ_API_KEY != "your_groq_api_key_here"),
        "elevenlabs_configured": bool(settings.ELEVENLABS_API_KEY and settings.ELEVENLABS_API_KEY != "your_elevenlabs_api_key_here"),
        "twilio_configured": bool(settings.TWILIO_ACCOUNT_SID)
    }

@app.get("/", tags=["UI"])
async def serve_ui():
    index_file = os.path.join(static_dir, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "Welcome to QwikCall AI Voice Interview Agent API"}
