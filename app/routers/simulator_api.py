"""
Web Simulator API — Powers the in-browser voice call simulator UI.
Supports: text turns, Deepgram audio STT turns, TTS audio generation, session management.
"""
import uuid
import tempfile
import os
from fastapi import APIRouter, Response, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from app.core.voice_agent import VoiceAgent, SESSIONS, _resolve_tenant_from_did
from app.core.tts_engine import generate_speech
from app.config import settings

router = APIRouter(prefix="/api/sim", tags=["Web Simulator"])


class TurnRequest(BaseModel):
    session_id: str
    user_speech: str
    tenant_id: str = "idigitalstudies"


@router.post("/start")
async def start_session(tenant_id: str = "idigitalstudies"):
    """Creates a new simulator session and returns the greeting text."""
    session_id = f"sim_{uuid.uuid4().hex[:8]}"
    agent = VoiceAgent(call_id=session_id, tenant_id=tenant_id, caller_number="web-simulator")
    greeting = agent.get_greeting()
    return {
        "session_id": session_id,
        "tenant_id": tenant_id,
        "greeting": greeting,
        "state": "GREETING"
    }


@router.post("/turn")
async def text_turn(req: TurnRequest):
    """Process a text-based conversation turn."""
    agent = VoiceAgent(call_id=req.session_id, tenant_id=req.tenant_id, caller_number="web-simulator")
    result = agent.process_turn(req.user_speech)
    return {
        "session_id": req.session_id,
        "user_speech": req.user_speech,
        "response": result["response"],
        "is_final": result["is_final"],
        "state": result.get("state", "MAIN_LOOP"),
        "escalate": result.get("escalate", False)
    }


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
    """Generates and returns spoken MP3 audio for the given text."""
    try:
        audio_bytes = await generate_speech(text, voice_name=voice)
        return Response(content=audio_bytes, media_type="audio/mpeg")
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
