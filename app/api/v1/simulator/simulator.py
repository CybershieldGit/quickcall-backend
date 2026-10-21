"""
Web Simulator API — Powers the in-browser voice call simulator UI.
Supports: text turns, Deepgram audio STT turns, TTS audio generation, session management.
"""
import re
import uuid
import tempfile
import os
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Response, HTTPException, UploadFile, File, Form, Query, Depends
from pydantic import BaseModel
from app.core.auth import get_current_tenant_auth, get_current_admin, resolve_tenant_scope, verify_tenant_access
from app.core.rate_limit import enforce_rate_limit
from app.core.voice_agent import (
    VoiceAgent, SESSIONS, _resolve_tenant_from_did, generate_executive_summary,
    _get_business_info, get_tenant_greeting
)
from app.core.tenant_service import load_tenant_config, normalize_tenant_id
from app.core.tts_engine import generate_speech, set_runtime_tts_engine
from app.config import settings

router = APIRouter(prefix="/api/sim", tags=["Web Simulator"])

_SIM_SESSION_RE = re.compile(r"^sim_[0-9a-f]{8,32}$")
_MAX_SIM_TEXT = 1000
_MAX_AUDIO_BYTES = 5 * 1024 * 1024


def _sim_session_id(requested: Optional[str], tenant_id: str) -> str:
    """Accepts only simulator session IDs that belong to the caller's tenant; otherwise starts a new one."""
    if requested and _SIM_SESSION_RE.match(requested):
        existing = SESSIONS.get(requested)
        if not existing or existing.get("tenant_id") == normalize_tenant_id(tenant_id):
            return requested
    elif requested:
        raise HTTPException(status_code=400, detail="Invalid simulator session_id.")
    return f"sim_{uuid.uuid4().hex[:16]}"


def _authorized_sim_session(session_id: str, auth: Dict[str, Any]) -> Dict[str, Any]:
    if not _SIM_SESSION_RE.match(session_id or ""):
        raise HTTPException(status_code=404, detail="Session not found")
    session = SESSIONS.get(session_id)
    if not session or not verify_tenant_access(session.get("tenant_id", ""), auth):
        raise HTTPException(status_code=404, detail="Session not found")
    return session


class TurnRequest(BaseModel):
    session_id: Optional[str] = None
    call_id: Optional[str] = None
    user_speech: Optional[str] = None
    text: Optional[str] = None
    query: Optional[str] = None
    message: Optional[str] = None
    tenant_id: Optional[str] = None


@router.post("/start")
def start_session(tenant_id: Optional[str] = None, auth: Dict[str, Any] = Depends(get_current_tenant_auth)):
    """Creates a new simulator session and returns the greeting text."""
    tenant_id = resolve_tenant_scope(auth, tenant_id)
    try:
        session_id = f"sim_{uuid.uuid4().hex[:16]}"
        agent = VoiceAgent(call_id=session_id, tenant_id=tenant_id, caller_number="web-simulator")
        greeting = agent.get_greeting()
        return {
            "session_id": session_id,
            "call_id": session_id,
            "tenant_id": tenant_id,
            "greeting": greeting,
            "state": "GREETING"
        }
    except Exception as e:
        print(f"[Simulator API] Start session error: {e}")
        raise HTTPException(status_code=500, detail="Failed to start simulator session.")


@router.post("/turn")
def text_turn(req: TurnRequest, auth: Dict[str, Any] = Depends(get_current_tenant_auth)):
    """Process a conversation turn with flexible payload support."""
    tenant_id = resolve_tenant_scope(auth, req.tenant_id)
    enforce_rate_limit(f"sim_turn:{tenant_id}", limit=120, window_seconds=600)
    call_id = _sim_session_id(req.session_id or req.call_id, tenant_id)
    speech = (req.query or req.message or req.user_speech or req.text or "").strip()[:_MAX_SIM_TEXT]
    try:
        agent = VoiceAgent(call_id=call_id, tenant_id=tenant_id, caller_number="web-simulator")
        result = agent.process_turn(speech)
        return {
            "session_id": call_id,
            "call_id": call_id,
            "user_speech": speech,
            "reply": result.get("response", ""),
            "response": result.get("response", ""),
            "agent_response": result.get("response", ""),
            "is_final": result.get("is_final", False),
            "state": result.get("state", "MAIN_LOOP"),
            "escalate": result.get("escalate", False)
        }
    except Exception as e:
        print(f"[Simulator API] Text turn error: {e}")
        raise HTTPException(status_code=500, detail="Error processing turn.")


@router.get("/resolve_did")
def test_resolve_did(
    phone_number: str = Query(..., description="Phone number or DID to test"),
    auth: Dict[str, Any] = Depends(get_current_tenant_auth)
):
    """Simulates inbound DID dialing to test multi-tenant phone routing."""
    clean_num = phone_number.strip()
    tid = _resolve_tenant_from_did(clean_num)
    if not verify_tenant_access(tid, auth):
        raise HTTPException(status_code=404, detail="This number is not routed to your workspace.")
    cfg = load_tenant_config(tid) or {}
    b_name, advisor = _get_business_info(tid, cfg)
    greeting = get_tenant_greeting(tid, cfg)
    esc_num = cfg.get("escalation_number", settings.DEFAULT_ESCALATION_NUMBER)
    return {
        "dialed_number": clean_num,
        "resolved_tenant_id": tid,
        "business_name": b_name,
        "advisor_name": advisor,
        "greeting": greeting,
        "escalation_number": esc_num,
        "persona_summary": cfg.get("persona", "")[:120] + "..." if len(cfg.get("persona", "")) > 120 else cfg.get("persona", "")
    }


@router.get("/tenants")
def list_simulator_tenants(auth: Dict[str, Any] = Depends(get_current_tenant_auth)):
    """Lists tenants visible to the caller (admin: all, reseller: own clients, tenant: itself) with bound numbers."""
    from sqlalchemy import text
    from app.db.session import sync_engine
    if auth.get("is_admin"):
        scope_sql, scope_params = "", {}
    elif auth.get("role") == "reseller":
        scope_sql, scope_params = "AND t.reseller_id = :rid", {"rid": auth.get("reseller_id")}
    else:
        scope_sql, scope_params = "AND t.tenant_id = :tid", {"tid": auth.get("tenant_id")}
    try:
        with sync_engine.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT t.tenant_id, t.business_name, t.plan, t.telephony_mode,
                       COALESCE(string_agg(DISTINCT nums.number, ', '), '') as numbers
                FROM tenants t
                LEFT JOIN (
                    SELECT tenant_id, number FROM tenant_phone_numbers WHERE is_active = true
                    UNION
                    SELECT assigned_tenant_id as tenant_id, number FROM phone_number_pool WHERE status = 'assigned'
                ) nums ON nums.tenant_id = t.tenant_id
                WHERE t.status = 'active' {scope_sql}
                GROUP BY t.tenant_id, t.business_name, t.plan, t.telephony_mode, t.created_at
                ORDER BY t.created_at DESC
            """), scope_params).fetchall()

        result = []
        for r in rows:
            result.append({
                "tenant_id": r[0],
                "business_name": r[1],
                "plan": r[2],
                "telephony_mode": r[3],
                "phone_numbers": [n.strip() for n in r[4].split(",") if n.strip()]
            })
        return {"tenants": result}
    except Exception as e:
        return {"tenants": []}


@router.get("/system-status")
async def get_system_status(auth: Dict[str, Any] = Depends(get_current_tenant_auth)):
    """Returns active TTS engine, voice model, and telephony readiness."""
    active_voice = (
        getattr(settings, "DEEPGRAM_VOICE_MODEL", "aura-asteria-en")
        if settings.TTS_ENGINE.lower() == "deepgram"
        else f"{getattr(settings, 'SARVAM_SPEAKER', 'ritu')} ({getattr(settings, 'SARVAM_MODEL', 'bulbul:v3')})"
    )
    return {
        "environment": getattr(settings, "ENVIRONMENT", "development"),
        "tts_engine": settings.TTS_ENGINE,
        "voice_model": active_voice,
        "groq_configured": bool(settings.GROQ_API_KEY),
        "deepgram_configured": bool(settings.DEEPGRAM_API_KEY),
        "sarvam_configured": bool(settings.SARVAM_API_KEY),
        "exotel_configured": bool(settings.EXOTEL_ACCOUNT_SID),
    }


@router.post("/set-tts")
async def set_tts(
    engine: str = Query(..., description="deepgram or sarvam"),
    admin_user: Dict[str, Any] = Depends(get_current_admin)
):
    """Dynamically toggle TTS engine between deepgram (testing) and sarvam (production)."""
    try:
        new_engine = set_runtime_tts_engine(engine)
        return {
            "status": "success",
            "active_tts_engine": new_engine,
            "message": f"TTS engine set to '{new_engine}'"
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.api_route("/preview-tts", methods=["GET", "POST"])
async def preview_tts_audio(
    text: str = Query("Hello! I am your AI counselor.", description="Text to synthesize for preview"),
    voice_engine: Optional[str] = Query(None, description="sarvam, deepgram, or edgetts"),
    voice_id: Optional[str] = Query(None, description="Voice speaker model ID"),
    language: Optional[str] = Query("hi-IN", description="Language code")
):
    """Generates and streams audio bytes for agent voice preview in portal dashboards."""
    from app.services.ai.tts_engine import preview_voice_speech
    audio_bytes = await preview_voice_speech(
        text=text,
        voice_engine=voice_engine,
        voice_id=voice_id,
        language=language
    )
    if not audio_bytes:
        raise HTTPException(status_code=500, detail="Could not synthesize preview audio.")
    
    media_type = "audio/mpeg" if audio_bytes.startswith(b"\xff\xfb") or audio_bytes.startswith(b"ID3") else "audio/wav"
    return Response(content=audio_bytes, media_type=media_type)



@router.get("/cache-questions")
def get_cache_questions(tenant_id: Optional[str] = None, auth: Dict[str, Any] = Depends(get_current_tenant_auth)):
    """Returns all FAQ questions currently vector-cached and frequency-tracked in Redis."""
    from app.core.semantic_cache import get_all_cached_questions
    if not (auth.get("is_admin") and not tenant_id):
        tenant_id = resolve_tenant_scope(auth, tenant_id)
    return get_all_cached_questions(tenant_id=tenant_id)



@router.post("/audio_turn")
def audio_turn(
    session_id: str = Form(...),
    tenant_id: Optional[str] = Form(default=None),
    file: UploadFile = File(...),
    auth: Dict[str, Any] = Depends(get_current_tenant_auth)
):
    """
    Accepts browser-recorded audio (webm/wav), transcribes via Deepgram v6, then processes agent turn.
    Deepgram SDK v6 API: client.listen.v1.media.transcribe_file(request=bytes, model=..., smart_format=True)
    """
    if not settings.DEEPGRAM_API_KEY:
        raise HTTPException(status_code=400, detail="DEEPGRAM_API_KEY is not configured.")
    tenant_id = resolve_tenant_scope(auth, tenant_id)
    enforce_rate_limit(f"sim_turn:{tenant_id}", limit=120, window_seconds=600)
    session_id = _sim_session_id(session_id, tenant_id)
    audio_content = file.file.read(_MAX_AUDIO_BYTES + 1)
    if len(audio_content) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio clip is too large.")

    tmp_path = None
    try:
        suffix = ".webm" if "webm" in (file.content_type or "") else ".wav"

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(audio_content)
            tmp_path = tmp.name

        # Deepgram SDK v6 — correct API
        from deepgram import DeepgramClient
        dg_client = DeepgramClient(api_key=settings.DEEPGRAM_API_KEY)

        with open(tmp_path, "rb") as af:
            audio_bytes = af.read()

        # transcribe_file takes keyword args directly (no Options object in v6)
        response = dg_client.listen.v1.media.transcribe_file(
            request=audio_bytes,
            model="nova-2",
            language="en",
            smart_format=True,
            punctuate=True
        )

        # Parse transcript from v6 response
        transcript = ""
        try:
            transcript = response.results.channels[0].alternatives[0].transcript.strip()
        except Exception:
            pass

        user_speech = transcript if transcript else "I didn't say anything"
        print(f"[Deepgram STT v6] Transcribed: '{user_speech}'")

        agent = VoiceAgent(call_id=session_id, tenant_id=tenant_id, caller_number="web-simulator")
        result = agent.process_turn(user_speech)

        return {
            "session_id": session_id,
            "user_speech": user_speech,
            "response": result["response"],
            "is_final": result["is_final"],
            "state": result.get("state", "MAIN_LOOP"),
            "escalate": result.get("escalate", False)
        }

    except Exception as e:
        print(f"[AudioTurn Error] {e}")
        raise HTTPException(status_code=500, detail="Error processing audio turn.")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


@router.get("/tts")
async def get_tts(text: str, voice: str = "en-US-AvaNeural", auth: Dict[str, Any] = Depends(get_current_tenant_auth)):
    """Generates and returns spoken audio (WAV/MP3) for the given text."""
    if len(text) > _MAX_SIM_TEXT:
        raise HTTPException(status_code=400, detail="Text is too long.")
    enforce_rate_limit(f"sim_tts:{auth.get('tenant_id') or auth.get('reseller_id') or 'admin'}", limit=120, window_seconds=600)
    try:
        audio_bytes = await generate_speech(text, voice_name=voice)
        media_type = "audio/wav" if audio_bytes.startswith(b"RIFF") else "audio/mpeg"
        return Response(content=audio_bytes, media_type=media_type)
    except Exception as e:
        print(f"[Simulator TTS Error] {e}")
        raise HTTPException(status_code=500, detail="Speech synthesis failed.")


@router.get("/session/{session_id}")
def get_session_info(session_id: str, auth: Dict[str, Any] = Depends(get_current_tenant_auth)):
    """Returns current state and history for a simulator session owned by the caller."""
    session = _authorized_sim_session(session_id, auth)
    return {
        "session_id": session_id,
        "state": session["state"],
        "tenant_id": session["tenant_id"],
        "turn_count": session["turn_index"],
        "history": session["history"]
    }


@router.post("/summarize/{session_id}")
def summarize_simulator_session(session_id: str, auth: Dict[str, Any] = Depends(get_current_tenant_auth)):
    """Generate or retrieve the AI executive brief note & summary for a simulator session."""
    session = _authorized_sim_session(session_id, auth)
    history = session.get("history")
    outcome = session.get("state", "completed")
    res = generate_executive_summary(call_id=session_id, history=history, outcome=outcome)
    return {"session_id": session_id, **res}
