import os
import traceback
import logging
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.api import api_router
from app.api.v1.chat import chat_router
from app.api.v1.simulator import simulator
from app.api.v1.telephony import outbound
from app.api.v1.portals import tenant_portal, admin_portal, reseller_portal

logger = logging.getLogger("qwickcall.main")

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Enterprise Multi-Tenant AI Voice Agent Platform (Exotel + Deepgram + Groq) — Whitelabel & Reseller Ready",
    version="2.2.0"
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

@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path in ("/", "/index.html", "/admin", "/admin/", "/reseller", "/reseller/", "/tenant", "/tenant/", "/client", "/client/", "/simulator", "/simulator/", "/docs-portal", "/docs-portal/", "/chats", "/chat", "/whatsapp", "/whatsapp-studio") or path.startswith(("/css", "/js", "/static", "/chats", "/chat", "/admin", "/reseller", "/tenant", "/client", "/simulator")):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

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
app.post("/api/v1/outbound/make-call", tags=["Outbound Calls"])(outbound.make_outbound_call)
app.post("/api/chat", tags=["Chat Agent"])(chat_router.send_chat_message)
app.post("/api/chat/message", tags=["Chat Agent"])(chat_router.send_chat_message)

# Direct login POST aliases for separate portal endpoints
app.post("/tenant/login", tags=["Tenant SaaS Portal"])(tenant_portal.login_tenant)
app.post("/client/login", tags=["Tenant SaaS Portal"])(tenant_portal.login_tenant)
app.post("/reseller/login", tags=["Reseller Partner Portal"])(reseller_portal.reseller_login)
app.post("/admin/login", tags=["SuperAdmin & Owner Portal"])(admin_portal.admin_login)

async def _background_keepalive():
    """Keeps Deepgram STT and TTS TLS connections hot every 15 seconds."""
    from app.api.v1.telephony.exotel_stream import keepalive_ping_stt
    from app.api.v1.telephony.sip_stream import keepalive_ping_sip_stt
    from app.services.ai.tts_engine import keepalive_ping_tts
    import asyncio
    while True:
        await asyncio.sleep(15.0)
        try:
            await asyncio.gather(keepalive_ping_stt(), keepalive_ping_sip_stt(), keepalive_ping_tts(), return_exceptions=True)
        except Exception as e:
            logger.debug(f"Keepalive ping exception: {e}")

@app.on_event("startup")
async def on_startup():
    import asyncio
    from app.services.knowledge import rag_engine
    from app.api.v1.telephony.exotel_stream import warmup_streaming_clients
    from app.api.v1.telephony.sip_stream import warmup_sip_stt
    from app.services.ai.tts_engine import warmup_tts
    from app.services.voice_agent.fillers import warmup_fillers
    from app.core.audio.sip_client import get_asterisk_sip_client
    print("🚀 Initializing Enterprise Voice Agent Platform...")

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
    try:
        asyncio.create_task(_background_keepalive())
    except Exception as e:
        print(f"⚠️ Keepalive task launch error: {e}")

    # Launch Asterisk WebRTC SIP Client if configured
    if settings.RPDIGITAL_SIP_USER and settings.RPDIGITAL_SIP_PASSWORD:
        try:
            asyncio.create_task(get_asterisk_sip_client().start())
            print(f"📞 Asterisk SIP WebRTC Client launched for user: {settings.RPDIGITAL_SIP_USER}")
        except Exception as e:
            print(f"⚠️ Asterisk SIP client startup error: {e}")
    else:
        print("ℹ️ Asterisk SIP WebRTC Client idle (Provide RPDIGITAL_SIP_USER & RPDIGITAL_SIP_PASSWORD in .env to activate).")

    if settings.TTS_ENGINE.lower() in ("edge-tts", "edgetts", "microsoft"):
        tts_voice = f"{settings.EDGE_TTS_VOICE_EN} / {settings.EDGE_TTS_VOICE_HI}"
    elif settings.TTS_ENGINE.lower() == "sarvam":
        tts_voice = f"{settings.SARVAM_SPEAKER} ({settings.SARVAM_MODEL})"
    else:
        tts_voice = getattr(settings, 'DEEPGRAM_VOICE_MODEL', 'aura-asteria-en')
    print(f"✅ TTS Engine active: {settings.TTS_ENGINE} ({tts_voice})")
    print("⚡ Pre-warmed & heartbeat-managed persistent TLS sockets for STT & TTS.")

@app.on_event("shutdown")
async def on_shutdown():
    try:
        from app.core.audio.sip_client import get_asterisk_sip_client
        await get_asterisk_sip_client().stop()
        print("🛑 Asterisk SIP WebRTC Client un-registered and stopped.")
    except Exception as e:
        print(f"⚠️ Error shutting down Asterisk SIP client: {e}")



static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    css_dir = os.path.join(static_dir, "css")
    if os.path.exists(css_dir):
        app.mount("/css", StaticFiles(directory=css_dir), name="css")
    js_dir = os.path.join(static_dir, "js")
    if os.path.exists(js_dir):
        app.mount("/js", StaticFiles(directory=js_dir), name="js")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)

@app.get("/health", tags=["System"])
async def health_check():
    from app.core.audio.sip_client import get_asterisk_sip_client
    from app.services.knowledge.semantic_cache import CACHE_STATS
    sc = get_asterisk_sip_client()
    if settings.TTS_ENGINE.lower() in ("edge-tts", "edgetts", "microsoft"):
        active_voice = f"{settings.EDGE_TTS_VOICE_EN} (Hindi: {settings.EDGE_TTS_VOICE_HI})"
    elif settings.TTS_ENGINE.lower() == "sarvam":
        active_voice = f"{settings.SARVAM_SPEAKER} ({settings.SARVAM_MODEL})"
    else:
        active_voice = getattr(settings, "DEEPGRAM_VOICE_MODEL", "aura-asteria-en")
    return {
        "status": "ok",
        "app": settings.PROJECT_NAME,
        "environment": getattr(settings, "ENVIRONMENT", "development"),
        "version": "2.1.0",
        "telephony": "exotel + rpdigital_asterisk",
        "tts_engine": settings.TTS_ENGINE,
        "voice_model": active_voice,
        "groq_configured": bool(settings.GROQ_API_KEY),
        "gemini_configured": bool(settings.GEMINI_API_KEY),
        "ram_fallback_configured": True,
        "deepgram_configured": bool(settings.DEEPGRAM_API_KEY),
        "exotel_configured": bool(settings.EXOTEL_ACCOUNT_SID),
        "redis_cache_configured": bool(settings.REDIS_URL),
        "redis_cache_stats": CACHE_STATS,
        "rpdigital_sip_running": sc.is_running,
        "rpdigital_sip_registered": sc.is_registered,
        "rpdigital_sip_user": sc.username,
        "database_url": settings.DATABASE_URL[:40] + "..."
    }

NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0"
}

def serve_html(filepath: str):
    if os.path.exists(filepath):
        return FileResponse(filepath, headers=NO_CACHE_HEADERS)
    return FileResponse(os.path.join(static_dir, "index.html"), headers=NO_CACHE_HEADERS)

# Separate Portal Login UI Routes
@app.api_route("/tenant/login", methods=["GET", "HEAD"], tags=["UI"])
@app.api_route("/tenant/login/", methods=["GET", "HEAD"], tags=["UI"])
@app.api_route("/client/login", methods=["GET", "HEAD"], tags=["UI"])
@app.api_route("/client/login/", methods=["GET", "HEAD"], tags=["UI"])
async def serve_tenant_login():
    return serve_html(os.path.join(static_dir, "login.html"))

@app.api_route("/reseller/login", methods=["GET", "HEAD"], tags=["UI"])
@app.api_route("/reseller/login/", methods=["GET", "HEAD"], tags=["UI"])
async def serve_reseller_login():
    return serve_html(os.path.join(static_dir, "login.html"))

@app.api_route("/admin/login", methods=["GET", "HEAD"], tags=["UI"])
@app.api_route("/admin/login/", methods=["GET", "HEAD"], tags=["UI"])
async def serve_admin_login():
    return serve_html(os.path.join(static_dir, "login.html"))

@app.api_route("/login", methods=["GET", "HEAD"], tags=["UI"])
@app.api_route("/login/", methods=["GET", "HEAD"], tags=["UI"])
@app.api_route("/signin", methods=["GET", "HEAD"], tags=["UI"])
@app.api_route("/auth", methods=["GET", "HEAD"], tags=["UI"])
async def serve_login_portal():
    return serve_html(os.path.join(static_dir, "login.html"))

@app.api_route("/admin", methods=["GET", "HEAD"], tags=["UI"])
@app.api_route("/admin/", methods=["GET", "HEAD"], tags=["UI"])
async def serve_admin_portal():
    return serve_html(os.path.join(static_dir, "admin.html"))

@app.api_route("/reseller", methods=["GET", "HEAD"], tags=["UI"])
@app.api_route("/reseller/", methods=["GET", "HEAD"], tags=["UI"])
async def serve_reseller_portal():
    return serve_html(os.path.join(static_dir, "reseller.html"))

@app.api_route("/", methods=["GET", "HEAD"], tags=["UI"])
@app.api_route("/tenant", methods=["GET", "HEAD"], tags=["UI"])
@app.api_route("/tenant/", methods=["GET", "HEAD"], tags=["UI"])
@app.api_route("/client", methods=["GET", "HEAD"], tags=["UI"])
@app.api_route("/client/", methods=["GET", "HEAD"], tags=["UI"])
async def serve_tenant_workspace():
    return serve_html(os.path.join(static_dir, "tenant.html"))

@app.api_route("/simulator", methods=["GET", "HEAD"], tags=["UI"])
@app.api_route("/simulator/", methods=["GET", "HEAD"], tags=["UI"])
@app.api_route("/docs-portal", methods=["GET", "HEAD"], tags=["UI"])
@app.api_route("/docs-portal/", methods=["GET", "HEAD"], tags=["UI"])
async def serve_sim_and_docs():
    index_file = os.path.join(static_dir, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "Voicebot Platform Sandbox"}

@app.api_route("/chats", methods=["GET", "HEAD"], tags=["UI"])
@app.api_route("/chats/", methods=["GET", "HEAD"], tags=["UI"])
@app.api_route("/chat", methods=["GET", "HEAD"], tags=["UI"])
@app.api_route("/chat/", methods=["GET", "HEAD"], tags=["UI"])
@app.api_route("/whatsapp", methods=["GET", "HEAD"], tags=["UI"])
@app.api_route("/whatsapp/", methods=["GET", "HEAD"], tags=["UI"])
@app.api_route("/whatsapp-studio", methods=["GET", "HEAD"], tags=["UI"])
async def serve_whatsapp_studio():
    wa_file = os.path.join(static_dir, "whatsapp.html")
    if os.path.exists(wa_file):
        return FileResponse(wa_file)
    index_file = os.path.join(static_dir, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "WhatsApp Studio not found"}
