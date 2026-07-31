import uuid
import tempfile
import os
from fastapi import APIRouter, Response, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from groq import Groq
from app.core.interview_agent import InterviewAgent, SESSIONS
from app.core.tts_engine import generate_speech
from app.config import settings

router = APIRouter(prefix="/api/sim", tags=["Web Simulator API"])

class TurnRequest(BaseModel):
    session_id: str
    user_speech: str

@router.post("/start")
async def start_sim_session():
    session_id = f"sim_{uuid.uuid4().hex[:8]}"
    agent = InterviewAgent(session_id=session_id)
    greeting = agent.get_greeting()
    
    return {
        "session_id": session_id,
        "greeting": greeting
    }

@router.post("/turn")
async def process_sim_turn(req: TurnRequest):
    if req.session_id not in SESSIONS:
        agent = InterviewAgent(session_id=req.session_id)
    else:
        agent = InterviewAgent(session_id=req.session_id)

    result = agent.process_turn(req.user_speech)
    return {
        "session_id": req.session_id,
        "user_speech": req.user_speech,
        "response": result["response"],
        "is_final": result["is_final"],
        "question_num": result["question_num"]
    }

@router.post("/audio_turn")
async def process_sim_audio_turn(
    session_id: str = Form(...),
    file: UploadFile = File(...)
):
    """
    Accepts raw recorded audio from browser MediaRecorder, transcribes it using Groq Whisper,
    and runs the interview agent turn.
    """
    if not settings.GROQ_API_KEY or settings.GROQ_API_KEY == "your_groq_api_key_here":
        raise HTTPException(status_code=400, detail="GROQ_API_KEY is required for audio transcription.")

    try:
        audio_content = await file.read()
        
        # Save temp audio file for Groq Whisper SDK
        suffix = ".webm" if "webm" in (file.content_type or "") else ".wav"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(audio_content)
            tmp_path = tmp.name

        # Call Groq Whisper API
        client = Groq(api_key=settings.GROQ_API_KEY)
        with open(tmp_path, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                file=(os.path.basename(tmp_path), audio_file.read()),
                model="whisper-large-v3",
                response_format="json",
                language="en"
            )

        # Cleanup temp file
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

        user_speech = transcription.text.strip()
        print(f"[Groq Whisper STT] Transcribed: '{user_speech}'")

        if not user_speech:
            user_speech = "No speech detected"

        # Process turn with InterviewAgent
        agent = InterviewAgent(session_id=session_id)
        result = agent.process_turn(user_speech)

        return {
            "session_id": session_id,
            "user_speech": user_speech,
            "response": result["response"],
            "is_final": result["is_final"],
            "question_num": result["question_num"]
        }

    except Exception as e:
        print(f"[AudioTurn Error]: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/tts")
async def get_tts_audio(text: str):
    """Generates MP3 audio for given text input."""
    try:
        audio_bytes = await generate_speech(text)
        return Response(content=audio_bytes, media_type="audio/mpeg")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
