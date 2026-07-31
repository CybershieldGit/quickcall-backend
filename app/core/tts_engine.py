import os
import httpx
import edge_tts
from typing import Optional
from app.config import settings

async def generate_speech(text: str, voice_name: str = "en-US-AvaNeural") -> bytes:
    """
    Generates spoken audio from text.
    Primary: ElevenLabs API (if ELEVENLABS_API_KEY is configured).
    Fallback: edge-tts (100% free Microsoft Neural voice synthesis, 0 API key required).
    Returns audio/mp3 byte payload.
    """
    if not text or not text.strip():
        text = "Hello from QwikCall."

    # Clean text of markdown characters
    clean_text = text.replace("*", "").replace("#", "").replace("`", "").strip()

    # Try ElevenLabs first if key is present
    if settings.ELEVENLABS_API_KEY and settings.ELEVENLABS_API_KEY != "your_elevenlabs_api_key_here":
        try:
            audio_bytes = await _generate_elevenlabs_speech(clean_text)
            if audio_bytes:
                return audio_bytes
        except Exception as e:
            print(f"[TTSEngine] ElevenLabs TTS error: {e}. Falling back to Edge-TTS.")

    # Edge-TTS Fallback (Zero cost, high quality)
    return await _generate_edge_tts_speech(clean_text, voice_name)

async def _generate_elevenlabs_speech(text: str) -> Optional[bytes]:
    voice_id = settings.ELEVENLABS_VOICE_ID or "21m00Tcm4TlvDq8ikWAM"
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": settings.ELEVENLABS_API_KEY
    }
    
    payload = {
        "text": text,
        "model_id": "eleven_monolingual_v1",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75
        }
    }
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.post(url, json=payload, headers=headers)
        if res.status_code == 200:
            return res.content
        else:
            print(f"[TTSEngine] ElevenLabs HTTP error {res.status_code}: {res.text}")
            return None

async def _generate_edge_tts_speech(text: str, voice_name: str) -> bytes:
    """Uses edge-tts to generate MP3 audio with zero API keys required."""
    communicate = edge_tts.Communicate(text, voice_name)
    audio_data = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_data.extend(chunk["data"])
    return bytes(audio_data)
