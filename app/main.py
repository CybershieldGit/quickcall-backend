import os
import asyncio
import traceback
import logging
from contextlib import asynccontextmanager
from typing import Dict, Any
from fastapi import FastAPI, Request, Response, HTTPException, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.core.auth import get_current_admin
from app.api import api_router
from app.api.v1.chat import chat_router
from app.api.v1.simulator import simulator
from app.api.v1.telephony import outbound, exotel_voice
from app.api.v1.portals import tenant_portal, admin_portal, reseller_portal

logger = logging.getLogger("qwickcall.main")


async def _background_keepalive():
    """Keeps Deepgram STT and TTS TLS connections hot every 15 seconds."""
    from app.api.v1.telephony.exotel_stream import keepalive_ping_stt
    from app.api.v1.telephony.sip_stream import keepalive_ping_sip_stt
    from app.services.ai.tts_engine import keepalive_ping_tts
    while True:
        await asyncio.sleep(15.0)
        try:
            await asyncio.gather(keepalive_ping_stt(), keepalive_ping_sip_stt(), keepalive_ping_tts(), return_exceptions=True)
        except Exception as e:
            logger.debug(f"Keepalive ping exception: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context manager handling startup initialization and graceful shutdown."""
    from app.services.knowledge import rag_engine
    from app.api.v1.telephony.exotel_stream import warmup_streaming_clients
    from app.api.v1.telephony.sip_stream import warmup_sip_stt
    from app.services.ai.tts_engine import warmup_tts
    from app.services.voice_agent.fillers import warmup_fillers
    from app.core.audio.sip_client import get_asterisk_sip_client, start_all_sip_clients, stop_all_sip_clients

    print("🚀 Initializing Enterprise Voice Agent Platform...")
    if not settings.EXOTEL_WEBHOOK_TOKEN:
        if settings.IS_PRODUCTION:
            logger.error("❌ EXOTEL_WEBHOOK_TOKEN is not set: all Exotel webhooks and /media-stream connections will be rejected.")
        else:
            logger.warning("⚠️ EXOTEL_WEBHOOK_TOKEN is not set: Exotel webhooks and /media-stream accept unauthenticated traffic.")
    if (settings.WHATSAPP_PROVIDER or "").lower() == "meta" and not settings.WHATSAPP_APP_SECRET:
        logger.warning("⚠️ WHATSAPP_APP_SECRET is not set: inbound Meta WhatsApp webhooks will be rejected.")
    if not settings.ADMIN_PASSWORD:
        logger.warning("⚠️ ADMIN_PASSWORD is not set: env-based owner login is disabled (admin_users table logins still work).")

    # Safe isolated warmup for each subsystem
    try:
        rag_engine.warmup_all_tenants()
    except Exception as e:
        print(f"⚠️ RAG warmup warning: {e}")

    try:
        await warmup_streaming_clients()
    except Exception as e:
        print(f"⚠️ Exotel STT warmup warning: {e}")

    try:
        await warmup_sip_stt()
    except Exception as e:
        print(f"⚠️ SIP STT warmup warning: {e}")

    try:
        await warmup_tts()
    except Exception as e:
        print(f"⚠️ TTS warmup warning: {e}")

    try:
        await warmup_fillers()
    except Exception as e:
        print(f"⚠️ Fillers warmup warning: {e}")

    # Initialize Redis Cloud Semantic Cache index and warm up local in-memory FAQ cache
    if settings.REDIS_URL:
        try:
            from app.services.knowledge.semantic_cache import ensure_index_created, warmup_local_faq_cache
            ensure_index_created()
            count = warmup_local_faq_cache()
            print(f"⚡ Redis Cloud Dynamic Semantic Cache index verified ({count} FAQs pre-cached in RAM).")
        except Exception as e:
            print(f"⚠️ Redis Semantic Cache startup warning: {e}")

    # Launch continuous heartbeat task
    keepalive_task = None
    try:
        keepalive_task = asyncio.create_task(_background_keepalive())
    except Exception as e:
        print(f"⚠️ Keepalive task launch error: {e}")

    # Launch Multi-SIP Client Manager for all active tenant extensions
    try:
        asyncio.create_task(start_all_sip_clients())
    except Exception as e:
        print(f"⚠️ Multi-SIP client startup error: {e}")

    if settings.TTS_ENGINE.lower() in ("edge-tts", "edgetts", "microsoft"):
        tts_voice = f"{settings.EDGE_TTS_VOICE_EN} / {settings.EDGE_TTS_VOICE_HI}"
    elif settings.TTS_ENGINE.lower() == "sarvam":
        tts_voice = f"{settings.SARVAM_SPEAKER} ({settings.SARVAM_MODEL})"
    else:
        tts_voice = getattr(settings, 'DEEPGRAM_VOICE_MODEL', 'aura-asteria-en')
    print(f"✅ TTS Engine active: {settings.TTS_ENGINE} ({tts_voice})")
    print("⚡ Pre-warmed & heartbeat-managed persistent TLS sockets for STT & TTS.")

    yield

    # Shutdown logic
    if keepalive_task:
        keepalive_task.cancel()
    try:
        await stop_all_sip_clients()
        print("🛑 All Asterisk SIP WebRTC Clients un-registered and stopped.")
    except Exception as e:
        print(f"⚠️ Error shutting down Asterisk SIP clients: {e}")


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Enterprise Multi-Tenant AI Voice Agent Platform (Exotel + Deepgram + Groq) — Whitelabel & Reseller Ready",
    version="2.2.0",
    lifespan=lifespan
)

origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
if not origins or "*" in origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )



# -----------------------------------------------------------------------------
# Global Exception Handlers
# -----------------------------------------------------------------------------
@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    """Formats HTTPException into standard structured error response with status and code."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": "error",
            "code": exc.status_code,
            "message": exc.detail if isinstance(exc.detail, str) else str(exc.detail),
            "path": request.url.path
        },
        headers=getattr(exc, "headers", None)
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Formats Pydantic request validation errors into clean user-friendly field-level diagnostics."""
    clean_errors = []
    for err in exc.errors():
        loc = " -> ".join([str(l) for l in err.get("loc", [])])
        msg = err.get("msg", "Invalid field value")
        clean_errors.append({"field": loc, "message": msg, "type": err.get("type")})
    return JSONResponse(
        status_code=422,
        content={
            "status": "error",
            "code": 422,
            "message": "Request validation failed. Please check the supplied parameters.",
            "errors": clean_errors,
            "path": request.url.path
        }
    )

@app.exception_handler(Exception)
async def global_unhandled_exception_handler(request: Request, exc: Exception):
    """Catches any unexpected internal server error, logs stack trace, and prevents server crashing."""
    err_trace = traceback.format_exc()
    logger.error(f"💥 Unhandled 500 error on {request.method} {request.url.path}: {exc}\n{err_trace}")
    print(f"\033[1;31m💥 [500 INTERNAL SERVER ERROR] {request.method} {request.url.path}: {exc}\033[0m")
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "code": 500,
            "message": "An unexpected internal server error occurred while processing the request.",
            "path": request.url.path
        }
    )

# Mount Consolidated Enterprise API Router
app.include_router(api_router)

# Legacy alias endpoints for backward compatibility
app.post("/api/v1/simulator/turn", tags=["Web Simulator"])(simulator.text_turn)
app.api_route("/api/v1/simulator/preview-tts", methods=["GET", "POST"], tags=["Web Simulator"])(simulator.preview_tts_audio)
app.post("/api/v1/outbound/make-call", tags=["Outbound Calls"])(outbound.make_outbound_call)
app.post("/api/chat", tags=["Chat Agent"])(chat_router.send_chat_message)
app.post("/api/chat/message", tags=["Chat Agent"])(chat_router.send_chat_message)
app.api_route("/api/v1/voicebot-connect", methods=["GET", "POST"], tags=["Exotel Voice Webhooks"])(exotel_voice.voicebot_dynamic_connect)
app.api_route("/voicebot-connect", methods=["GET", "POST"], tags=["Exotel Voice Webhooks"])(exotel_voice.voicebot_dynamic_connect)
app.api_route("/api/v1/passthru", methods=["GET", "POST"], tags=["Exotel Voice Webhooks"])(exotel_voice.exotel_passthru)
app.api_route("/passthru", methods=["GET", "POST"], tags=["Exotel Voice Webhooks"])(exotel_voice.exotel_passthru)

# Direct login POST aliases for separate portal endpoints
app.post("/tenant/login", tags=["Tenant SaaS Portal"])(tenant_portal.login_tenant)
app.post("/client/login", tags=["Tenant SaaS Portal"])(tenant_portal.login_tenant)
app.post("/reseller/login", tags=["Reseller Partner Portal"])(reseller_portal.reseller_login)
app.post("/admin/login", tags=["SuperAdmin & Owner Portal"])(admin_portal.admin_login)


@app.get("/api/info", tags=["System"])
async def api_info():
    """Service status banner for the backend API."""
    return {
        "status": "online",
        "service": settings.PROJECT_NAME,
        "version": "2.2.0",
        "health": "/health",
        "docs": "/docs"
    }

@app.get("/", tags=["Frontend SPA"])
async def root(request: Request):
    """Serves the single-page application frontend or status JSON if JSON requested."""
    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        return await api_info()
    static_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "index.html")
    if os.path.isfile(static_file):
        return FileResponse(static_file)
    return await api_info()

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)

@app.get("/health", tags=["System"])
async def health_check():
    """Public liveness probe. Deliberately exposes no configuration, credentials, or tenant data."""
    return {"status": "ok", "version": "2.2.0"}


@app.get("/api/v1/admin/system-health", tags=["SuperAdmin & Owner Portal"])
async def detailed_health_check(admin_user: Dict[str, Any] = Depends(get_current_admin)):
    """Detailed subsystem status for the platform owner."""
    from app.core.audio.sip_client import get_asterisk_sip_client, get_all_sip_clients
    from app.services.knowledge.semantic_cache import CACHE_STATS
    sc = get_asterisk_sip_client()
    all_clients = get_all_sip_clients()
    return {
        "status": "ok",
        "environment": settings.ENVIRONMENT,
        "tts_engine": settings.TTS_ENGINE,
        "groq_configured": bool(settings.GROQ_API_KEY),
        "gemini_configured": bool(settings.GEMINI_API_KEY),
        "deepgram_configured": bool(settings.DEEPGRAM_API_KEY),
        "exotel_configured": bool(settings.EXOTEL_ACCOUNT_SID),
        "exotel_webhook_token_configured": bool(settings.EXOTEL_WEBHOOK_TOKEN),
        "whatsapp_signature_verification": bool(settings.WHATSAPP_APP_SECRET),
        "redis_cache_configured": bool(settings.REDIS_URL),
        "redis_cache_stats": CACHE_STATS,
        "rpdigital_sip_running": any(c.is_running for c in all_clients) if all_clients else sc.is_running,
        "rpdigital_sip_registered": any(c.is_registered for c in all_clients) if all_clients else sc.is_registered,
        "rpdigital_registered_extensions": [c.username for c in all_clients if c.is_registered],
    }


# -----------------------------------------------------------------------------
# Static Files & SPA Routing Fallback
# -----------------------------------------------------------------------------
_static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
if os.path.isdir(_static_dir):
    _assets_dir = os.path.join(_static_dir, "assets")
    if os.path.isdir(_assets_dir):
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        if full_path.startswith("api") or full_path.startswith("docs") or full_path.startswith("openapi") or full_path.startswith("ws") or full_path.startswith("health"):
            raise HTTPException(status_code=404, detail="API route not found.")
        target_file = os.path.join(_static_dir, full_path)
        if os.path.isfile(target_file):
            return FileResponse(target_file)
        index_file = os.path.join(_static_dir, "index.html")
        if os.path.isfile(index_file):
            return FileResponse(index_file)
        raise HTTPException(status_code=404, detail="File not found.")
