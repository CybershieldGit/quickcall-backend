import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.routers import twilio_voice, simulator_api, outbound_call

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Enterprise Multi-Tenant AI Voice Agent Platform — Pilot: iDigitalStudies",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(twilio_voice.router)
app.include_router(simulator_api.router)
app.include_router(outbound_call.router)

@app.on_event("startup")
async def on_startup():
    from app.core import rag_engine
    rag_engine.warmup_rag("idigitalstudies")


static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/health", tags=["System"])
async def health_check():
    return {
        "status": "ok",
        "app": settings.PROJECT_NAME,
        "version": "2.0.0",
        "groq_configured": bool(settings.GROQ_API_KEY),
        "deepgram_configured": bool(settings.DEEPGRAM_API_KEY),
        "twilio_configured": bool(settings.TWILIO_ACCOUNT_SID),
        "database_url": settings.DATABASE_URL[:40] + "..."
    }

@app.get("/", tags=["UI"])
async def serve_ui():
    index_file = os.path.join(static_dir, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "Welcome to Enterprise Voice Agent Platform v2.0"}
