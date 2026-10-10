"""
Web Simulator API — Powers the in-browser voice call simulator UI.
Supports: text turns, Deepgram audio STT turns, TTS audio generation, session management.
"""
import uuid
import tempfile
import os
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Response, HTTPException, UploadFile, File, Form, Query
from pydantic import BaseModel
from app.core.voice_agent import (
    VoiceAgent, SESSIONS, _resolve_tenant_from_did, generate_executive_summary,
    _get_business_info, get_tenant_greeting
)
from app.core.tenant_service import load_tenant_config
from app.core.tts_engine import generate_speech, set_runtime_tts_engine
from app.config import settings

router = APIRouter(prefix="/api/sim", tags=["Web Simulator"])


class TurnRequest(BaseModel):
    session_id: Optional[str] = None
    call_id: Optional[str] = None
    user_speech: Optional[str] = None
    text: Optional[str] = None
    query: Optional[str] = None
    message: Optional[str] = None
    tenant_id: str = "idigitalstudies"


@router.post("/start")
async def start_session(tenant_id: str = "idigitalstudies"):
    """Creates a new simulator session and returns the greeting text."""
    try:
        session_id = f"sim_{uuid.uuid4().hex[:8]}"
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
        raise HTTPException(status_code=500, detail=f"Failed to start simulator session: {str(e)}")


@router.post("/turn")
async def text_turn(req: TurnRequest):
    """Process a conversation turn with flexible payload support."""
    try:
        call_id = req.session_id or req.call_id or f"sim_{uuid.uuid4().hex[:8]}"
        speech = (req.query or req.message or req.user_speech or req.text or "").strip()
        agent = VoiceAgent(call_id=call_id, tenant_id=req.tenant_id, caller_number="web-simulator")
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
        raise HTTPException(status_code=500, detail=f"Error processing turn: {str(e)}")


@router.get("/resolve_did")
async def test_resolve_did(phone_number: str = Query(..., description="Phone number or DID to test")):
    """Simulates inbound DID dialing to test multi-tenant phone routing."""
    clean_num = phone_number.strip()
    tid = _resolve_tenant_from_did(clean_num)
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
async def list_simulator_tenants():
    """Lists available tenants for testing with their bound phone numbers."""
    from sqlalchemy import text
    from app.db.session import sync_engine
    try:
        with sync_engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT t.tenant_id, t.business_name, t.plan, t.telephony_mode,
                       COALESCE(string_agg(DISTINCT nums.number, ', '), '') as numbers
                FROM tenants t
                LEFT JOIN (
                    SELECT tenant_id, number FROM tenant_phone_numbers WHERE is_active = true
                    UNION
                    SELECT assigned_tenant_id as tenant_id, number FROM phone_number_pool WHERE status = 'assigned'
                ) nums ON nums.tenant_id = t.tenant_id
                WHERE t.status = 'active'
                GROUP BY t.tenant_id, t.business_name, t.plan, t.telephony_mode, t.created_at
                ORDER BY t.created_at DESC
            """)).fetchall()

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
async def get_system_status():
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
        "default_escalation_number": settings.DEFAULT_ESCALATION_NUMBER
    }


@router.post("/set-tts")
async def set_tts(engine: str = Query(..., description="deepgram or sarvam")):
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


@router.get("/cache-questions")
async def get_cache_questions(tenant_id: Optional[str] = None):
    """Returns all FAQ questions currently vector-cached and frequency-tracked in Redis."""
    from app.core.semantic_cache import get_all_cached_questions
    return get_all_cached_questions(tenant_id=tenant_id)



@router.post("/audio_turn")
async def audio_turn(
    session_id: str = Form(...),
    tenant_id: str = Form(default="idigitalstudies"),
    file: UploadFile = File(...)
):
    """
    Accepts browser-recorded audio (webm/wav), transcribes via Deepgram v6, then processes agent turn.
    Deepgram SDK v6 API: client.listen.v1.media.transcribe_file(request=bytes, model=..., smart_format=True)
    """
    if not settings.DEEPGRAM_API_KEY:
        raise HTTPException(status_code=400, detail="DEEPGRAM_API_KEY is not configured.")

    tmp_path = None
    try:
        audio_content = await file.read()
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
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


@router.get("/tts")
async def get_tts(text: str, voice: str = "en-US-AvaNeural"):
    """Generates and returns spoken audio (WAV/MP3) for the given text."""
    try:
        audio_bytes = await generate_speech(text, voice_name=voice)
        media_type = "audio/wav" if audio_bytes.startswith(b"RIFF") else "audio/mpeg"
        return Response(content=audio_bytes, media_type=media_type)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/session/{session_id}")
async def get_session_info(session_id: str):
    """Returns current state and history for a session (useful for debug/transcript view)."""
    if session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail="Session not found")
    session = SESSIONS[session_id]
    return {
        "session_id": session_id,
        "state": session["state"],
        "tenant_id": session["tenant_id"],
        "turn_count": session["turn_index"],
        "history": session["history"]
    }


@router.post("/summarize/{session_id}")
async def summarize_simulator_session(session_id: str):
    """Generate or retrieve the AI executive brief note & summary for a simulator session."""
    session = SESSIONS.get(session_id)
    history = session.get("history") if session else None
    outcome = session.get("state", "completed") if session else "completed"
    res = generate_executive_summary(call_id=session_id, history=history, outcome=outcome)
    return {"session_id": session_id, **res}
