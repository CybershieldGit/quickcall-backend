"""
Enterprise TTS Pipeline for QwickCall
Primary: Deepgram Aura TTS (16-bit Linear PCM for Exotel streaming)
Fallback: ElevenLabs / Microsoft Edge-TTS Neural Voice
"""
import io
import re
import struct
import base64
import asyncio
from typing import Optional, List
import json
import httpx
import edge_tts
import websockets
import av

from app.core.audio_utils import (
    pcm_rms, resample_pcm_16k_to_8k, OfficeAcousticsFilter, apply_office_acoustics
)
from collections import OrderedDict
from app.config import settings

_HTTP_CLIENT: Optional[httpx.AsyncClient] = None
_AUDIO_CHUNK_CACHE: OrderedDict = OrderedDict()
MAX_TTS_CACHE_ENTRIES: int = 300

def _get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(4.0, connect=1.5),
            limits=httpx.Limits(max_keepalive_connections=25, max_connections=50, keepalive_expiry=15.0)
        )
    return _HTTP_CLIENT


def get_graceful_transfer_message(language: Optional[str] = None) -> str:
    """Returns a natural, reassuring message informing the caller that they are being transferred due to a connection issue."""
    lang = (language or "hi-IN").lower()
    if lang.startswith("en"):
        return "I'm sorry, I'm having trouble connecting right now. Let me transfer you directly to our counselor. Please stay on the line."
    return "Kshama kijiye, mujhe connect karne mein samasya aa rahi hai. Main aapki call turant counselor ko transfer kar rahi hoon, kripya line par bane rahiye."


def get_inaudible_prompt_message(language: Optional[str] = None) -> str:
    """Returns a polite prompt asking the caller to repeat when audio is faint or inaudible."""
    lang = (language or "hi-IN").lower()
    if lang.startswith("en"):
        return "Hello? I'm sorry, I couldn't hear you clearly. Could you please repeat that?"
    return "Ji hello? Kripya dobara boliye, aapki aawaz theek se nahi aa rahi thi."


def is_tts_cached(text: str, target_sample_rate: int = 8000, language_code: Optional[str] = None) -> bool:
    """Checks if audio for this exact text and settings is already cached in RAM (0ms latency, ₹0.00 cost)."""
    clean_text = clean_text_for_speech(text)
    if not clean_text:
        return False
    engine = getattr(settings, "TTS_ENGINE", "deepgram").lower()
    speaker = getattr(settings, "SARVAM_SPEAKER", "ritu") if engine == "sarvam" else getattr(settings, "DEEPGRAM_VOICE_MODEL", "aura-asteria-en")
    use_acoustics = getattr(settings, "ENABLE_OFFICE_ACOUSTICS", False)
    wet_mix = getattr(settings, "OFFICE_ACOUSTICS_WET", 0.0)
    use_headset = getattr(settings, "ENABLE_HEADSET_OFFICE_SOUND", False)
    amb_level = getattr(settings, "OFFICE_AMBIENCE_LEVEL", 0.0) if use_headset else 0.0
    cache_key = f"{clean_text.lower().strip()}_{target_sample_rate}_{language_code or ''}_{speaker}_{engine}_{use_acoustics}_{wet_mix}_{use_headset}_{amb_level}"
    return cache_key in _AUDIO_CHUNK_CACHE


async def warmup_tts():
    """Pre-warms persistent TLS connection to TTS and pre-caches common greetings and emergency fallbacks for 0ms, 0-cost playback."""
    try:
        common_greetings = [
            # Active tenant greetings
            "Hello, I am Aanya from iDigital Studies, how can I help you?",
            "Hello! Welcome to iDigitalStudies admissions office. How can I help you today?",
            "Namaste! iDigitalStudies mein aapka swagat hai. Main aapki kya madad kar sakti hoon?",
            "Hello, I am Rohan from Phono Tel, how can I help you?",
            # Language switch confirmations
            "Ji bilkul! Ab main Hindi mein baat karungi. Aap digital marketing course ke baare mein kya janna chahte hain?",
            "Sure, I will continue in English. How can I assist you with our courses today?",
            # Common affirmative / follow-up transitions
            "Ji bilkul, main aapko details batati hoon.",
            "Sure, let me check that for you.",
            # Emergency fail-safes
            get_graceful_transfer_message("en-IN"),
            get_graceful_transfer_message("hi-IN"),
            get_inaudible_prompt_message("en-IN"),
            get_inaudible_prompt_message("hi-IN"),
        ]
        for greeting in common_greetings:
            async for _ in stream_synthesize_pcm(greeting, target_sample_rate=8000):
                pass
        print(f"[TTSEngine] Warmup complete: {len(_AUDIO_CHUNK_CACHE)} static greetings & fail-safe emergency prompts pre-cached in RAM (0 API cost on playback).")
    except Exception as e:
        print(f"[TTSEngine Warmup Exception] {e}")


async def keepalive_ping_tts():
    """Background heartbeat ping to keep TTS TLS connection hot at all times."""
    try:
        client = _get_http_client()
        if getattr(settings, "TTS_ENGINE", "deepgram").lower() == "sarvam" and getattr(settings, "SARVAM_API_KEY", None):
            headers = {"api-subscription-key": settings.SARVAM_API_KEY}
            await client.options("https://api.sarvam.ai/text-to-speech", headers=headers)
        elif settings.DEEPGRAM_API_KEY:
            headers = {"Authorization": f"Token {settings.DEEPGRAM_API_KEY}"}
            await client.head("https://api.deepgram.com/v1/projects", headers=headers)
    except Exception:
        pass


def clean_text_for_speech(text: str) -> str:
    """Strip markdown formatting, asterisks, bullet points, brackets."""
    if not text:
        return ""
    text = re.sub(r'[*#`_~]', '', text)
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)  # Markdown links
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def chunk_pcm_audio(pcm_data: bytes, chunk_size: int = 640) -> List[bytes]:
    """
    Splits 16-bit linear PCM byte stream into exact chunks that are multiples of 320 bytes.
    Standard: 640 bytes = 40ms of 8000Hz 16-bit mono PCM.
    Exotel requirement: Multiple of 320 bytes (between 3.2KB and 100KB for full burst, or 320-1280 for real-time pacing).
    """
    chunks = []
    chunk_size = max(320, (chunk_size // 320) * 320)
    for i in range(0, len(pcm_data), chunk_size):
        chunk = pcm_data[i:i + chunk_size]
        rem = len(chunk) % 320
        if rem != 0:
            chunk = chunk + (b'\x00' * (320 - rem))
        chunks.append(chunk)
    return chunks


def clean_pcm_audio(raw_pcm: bytes, fade_samples: int = 40) -> bytes:
    """
    1. Strips any RIFF/WAV headers if present (eliminates loud click/pop/buzz sound on phone).
    2. Applies a smooth 5ms micro-fade-in/out to prevent audio step discontinuity clicks.
    """
    if not raw_pcm:
        return b""
    
    # Strip WAV/RIFF header if present
    if raw_pcm.startswith(b"RIFF"):
        data_pos = raw_pcm.find(b"data")
        if data_pos != -1 and len(raw_pcm) > data_pos + 8:
            raw_pcm = raw_pcm[data_pos + 8:]
        elif len(raw_pcm) > 44:
            raw_pcm = raw_pcm[44:]

    # Micro fade-in/out to prevent speaker click on phone lines
    if len(raw_pcm) >= fade_samples * 4:
        num_samples = len(raw_pcm) // 2
        try:
            samples = list(struct.unpack(f"<{num_samples}h", raw_pcm[:num_samples*2]))
            for i in range(min(fade_samples, num_samples)):
                factor = i / float(fade_samples)
                samples[i] = int(samples[i] * factor)
                idx = num_samples - 1 - i
                samples[idx] = int(samples[idx] * factor)
            return struct.pack(f"<{num_samples}h", *samples)
        except Exception:
            pass

    return raw_pcm


def _detect_sarvam_language(text: str) -> str:
    """
    Bilingual language detector: returns 'hi-IN' for Hindi/Hinglish/Devanagari,
    otherwise 'en-IN' for English.
    """
    if not text:
        return getattr(settings, "SARVAM_LANGUAGE_CODE", "hi-IN")

    # 1. Native Devanagari script presence -> always hi-IN
    if any('\u0900' <= c <= '\u097F' for c in text):
        return "hi-IN"

    # 2. Explicit Hindi/English requests
    lower_text = text.lower().strip()
    if re.search(r'\b(?:hindi|hinglish|in hindi|bol.*hindi|baat.*hindi)\b', lower_text):
        return "hi-IN"
    if re.search(r'\b(?:english|in english|only english)\b', lower_text):
        return "en-IN"

    # 3. Common Romanized Hinglish vocabulary
    tokens = set(re.findall(r'\b[a-zA-Z]+\b', lower_text))
    hinglish_keywords = {
        "namaste", "aap", "aapka", "aapko", "kya", "madad", "hamare", "yahan",
        "hai", "hain", "karein", "bataiye", "batao", "batado", "shukriya", "dhanyawad", "ji", "haanji",
        "bilkul", "zaroor", "kitna", "kitni", "kaise", "padhai", "sikhna",
        "chahiye", "karna", "hoga", "hogi", "baat", "suno", "theek", "batati",
        "samjha", "deti", "karti", "hoon", "paise", "karenge", "milega", "bolo",
        "bolte", "accha", "achha", "karo", "pata", "bhi", "bahut", "mujhe", "mera", "meri", "kuch"
    }
    if len(tokens.intersection(hinglish_keywords)) >= 1:
        return "hi-IN"

    return "en-IN"


def is_indic_text(text: str) -> bool:
    """Returns True if text is in Hindi or Hinglish."""
    return _detect_sarvam_language(text) != "en-IN"


async def _stream_edge_tts_pcm(text: str, target_sample_rate: int = 8000):
    """
    True real-time streaming TTS generator for Microsoft Neural Voice.
    Yields 640-byte 16-bit linear PCM audio chunks the instant MP3 packets arrive,
    eliminating batch waiting latency.
    """
    if any('\u0900' <= char <= '\u097F' for char in text):
        voice = getattr(settings, "EDGE_TTS_VOICE_HI", "hi-IN-SwaraNeural")
    else:
        voice = getattr(settings, "EDGE_TTS_VOICE_EN", "en-IN-NeerjaNeural")

    try:
        import av
        comm = edge_tts.Communicate(text, voice)
        codec = av.CodecContext.create('mp3', 'r')
        resampler = av.AudioResampler(format='s16', layout='mono', rate=target_sample_rate)
        
        buffer = bytearray()
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                packets = codec.parse(chunk["data"])
                for packet in packets:
                    frames = codec.decode(packet)
                    for frame in frames:
                        for res_frame in resampler.resample(frame):
                            raw_pcm = res_frame.to_ndarray().tobytes()
                            buffer.extend(raw_pcm)
                            while len(buffer) >= 640:
                                send_chunk = bytes(buffer[:640])
                                del buffer[:640]
                                yield send_chunk
        
        if buffer:
            remainder = len(buffer) % 320
            if remainder != 0:
                buffer.extend(b"\x00" * (320 - remainder))
            yield bytes(buffer)
            
    except Exception as e:
        print(f"[TTSEngine] Streaming Edge-TTS error: {e}")


async def _synthesize_edge_tts_pcm(text: str, target_sample_rate: int = 8000) -> bytes:
    """Collects streaming chunks into a single byte array for non-streaming endpoints."""
    chunks = []
    async for c in _stream_edge_tts_pcm(text, target_sample_rate=target_sample_rate):
        chunks.append(c)
    return b"".join(chunks)


async def _synthesize_sarvam_rest_pcm(clean_text: str, target_sample_rate: int = 8000, language_code: Optional[str] = None) -> bytes:
    """Fallback: HTTP REST synthesis via Sarvam AI."""
    if not getattr(settings, "SARVAM_API_KEY", None):
        return b""
    speaker = getattr(settings, "SARVAM_SPEAKER", "ritu")
    model = getattr(settings, "SARVAM_MODEL", "bulbul:v3")
    lang = language_code or _detect_sarvam_language(clean_text)
    url = "https://api.sarvam.ai/text-to-speech"
    headers = {
        "api-subscription-key": settings.SARVAM_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "inputs": [clean_text],
        "target_language_code": lang,
        "speaker": speaker,
        "speech_sample_rate": target_sample_rate,
        "model": model
    }
    for attempt in range(2):
        try:
            client = _get_http_client()
            res = await client.post(url, headers=headers, json=payload)
            if res.status_code == 200:
                data = res.json()
                audios = data.get("audios", [])
                if audios:
                    raw_audio = base64.b64decode(audios[0])
                    return clean_pcm_audio(raw_audio)
            else:
                print(f"[TTSEngine] Sarvam AI REST error ({res.status_code}): {res.text}")
                break
        except Exception as e:
            print(f"[TTSEngine] Sarvam AI REST error: {e}")
            break
    return b""


async def _stream_sarvam_ws_pcm(clean_text: str, target_sample_rate: int = 8000, language_code: Optional[str] = None):
    """
    Ultra-low latency streaming TTS using Sarvam AI WebSocket API.
    Streams 16-bit linear PCM audio chunks (640 bytes) with Time-To-First-Byte of ~220-450ms.
    Decodes audio/mpeg on the fly using PyAV to eliminate buffer latency.
    Uses caller's session language_code if provided to prevent sentence-by-sentence voice flipping.
    """
    if not getattr(settings, "SARVAM_API_KEY", None):
        return

    url = f"wss://api.sarvam.ai/text-to-speech/ws?model={getattr(settings, 'SARVAM_MODEL', 'bulbul:v3')}&send_completion_event=true"
    headers = {"Api-Subscription-Key": settings.SARVAM_API_KEY}
    speaker = getattr(settings, "SARVAM_SPEAKER", "ritu")
    lang = language_code or _detect_sarvam_language(clean_text)

    try:
        async with websockets.connect(
            url,
            additional_headers=headers,
            ping_interval=15,
            ping_timeout=10,
            close_timeout=5
        ) as ws:
            config = {
                "type": "config",
                "data": {
                    "language_code": lang,
                    "speaker": speaker,
                    "pace": 1.05
                }
            }
            await ws.send(json.dumps(config))
            await ws.send(json.dumps({"type": "text", "data": {"text": clean_text}}))
            await ws.send(json.dumps({"type": "flush"}))

            codec = av.CodecContext.create('mp3', 'r')
            resampler = av.AudioResampler(format='s16', layout='mono', rate=target_sample_rate)
            buffer = bytearray()

            while True:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=3.5)
                except asyncio.TimeoutError:
                    print("[TTSEngine] Sarvam WS recv timed out (3.5s), finishing current buffer")
                    break
                data = json.loads(msg)
                mtype = data.get("type")
                if mtype == "audio":
                    raw_b64 = data.get("data", {}).get("audio", "")
                    if not raw_b64:
                        continue
                    mp3_data = base64.b64decode(raw_b64)
                    for packet in codec.parse(mp3_data):
                        for frame in codec.decode(packet):
                            for res_frame in resampler.resample(frame):
                                buffer.extend(res_frame.to_ndarray().tobytes())
                                while len(buffer) >= 640:
                                    send_chunk = bytes(buffer[:640])
                                    del buffer[:640]
                                    yield send_chunk
                elif mtype == "event" and data.get("data", {}).get("event_type") == "final":
                    break
                elif mtype == "error":
                    print(f"[TTSEngine] Sarvam WS error message: {data}")
                    break

            if buffer:
                remainder = len(buffer) % 320
                if remainder != 0:
                    buffer.extend(b"\x00" * (320 - remainder))
                yield bytes(buffer)

    except Exception as e:
        print(f"[TTSEngine] Sarvam WS streaming exception ({e}), falling back to REST")
        rest_pcm = await _synthesize_sarvam_rest_pcm(clean_text, target_sample_rate=target_sample_rate, language_code=lang)
        if rest_pcm:
            for chunk in chunk_pcm_audio(rest_pcm, chunk_size=640):
                yield chunk


def _postprocess_pcm(pcm: bytes, target_sample_rate: int = 8000) -> bytes:
    """Applies headset mic frequency response, desk early reflections, and background counseling floor chatter."""
    if not pcm:
        return b""
    if getattr(settings, "ENABLE_OFFICE_ACOUSTICS", False):
        wet = getattr(settings, "OFFICE_ACOUSTICS_WET", 0.0)
        use_headset = getattr(settings, "ENABLE_HEADSET_OFFICE_SOUND", False)
        amb_level = getattr(settings, "OFFICE_AMBIENCE_LEVEL", 0.0) if use_headset else 0.0
        pcm = apply_office_acoustics(
            pcm,
            sample_rate=target_sample_rate,
            wet_mix=wet,
            ambience_level=amb_level,
            headset_eq=use_headset
        )
    return pcm


async def synthesize_pcm(text: str, target_sample_rate: int = 8000, language_code: Optional[str] = None) -> bytes:
    """
    Synthesizes speech into 16-bit Linear PCM (mono, little-endian) at target_sample_rate (default 8000Hz for Exotel/SIP).
    Controlled by settings.TTS_ENGINE:
    1. 'deepgram' -> Deepgram Aura TTS (default for development & testing)
    2. 'sarvam'   -> Sarvam AI (ritu, bulbul:v3 via WebSocket streaming or REST - production Indian voice)
    3. 'edge-tts' -> Microsoft Neural Voice (en-IN-NeerjaNeural / hi-IN-SwaraNeural) [Zero cost, studio quality]
    """
    clean_text = clean_text_for_speech(text)
    if not clean_text:
        return b""

    engine = getattr(settings, "TTS_ENGINE", "deepgram").lower()

    # 1. Primary when deepgram (Development & Testing)
    if engine == "deepgram" and settings.DEEPGRAM_API_KEY:
        voice_model = getattr(settings, "DEEPGRAM_VOICE_MODEL", "aura-asteria-en")
        url = f"https://api.deepgram.com/v1/speak?model={voice_model}&encoding=linear16&sample_rate={target_sample_rate}&container=none"
        headers = {
            "Authorization": f"Token {settings.DEEPGRAM_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {"text": clean_text}
        
        for attempt in range(2):
            try:
                client = _get_http_client()
                res = await client.post(url, headers=headers, json=payload)
                if res.status_code == 200 and len(res.content) > 0:
                    cleaned = clean_pcm_audio(res.content) + (b"\x00" * 640 * 3)
                    return _postprocess_pcm(cleaned, target_sample_rate)
                else:
                    print(f"[TTSEngine] Deepgram Aura TTS error ({res.status_code}): {res.text}")
                    break
            except RuntimeError as re:
                if "Event loop is closed" in str(re):
                    global _HTTP_CLIENT
                    _HTTP_CLIENT = None
                    continue
                print(f"[TTSEngine] Deepgram TTS runtime error: {re}")
                break
            except Exception as e:
                print(f"[TTSEngine] Deepgram TTS error: {e}")
                break

    # 2. Primary when edge-tts: Microsoft Neural Voice (en-IN-NeerjaNeural / hi-IN-SwaraNeural)
    if engine in ("edge-tts", "edgetts", "microsoft"):
        pcm = await _synthesize_edge_tts_pcm(clean_text, target_sample_rate=target_sample_rate)
        if pcm:
            return _postprocess_pcm(pcm, target_sample_rate)

    # 3. Primary when sarvam (Production) OR Indic fallback
    if (engine == "sarvam" or (engine != "deepgram" and is_indic_text(clean_text))) and getattr(settings, "SARVAM_API_KEY", None):
        chunks = []
        async for c in _stream_sarvam_ws_pcm(clean_text, target_sample_rate=target_sample_rate, language_code=language_code):
            chunks.append(c)
        if chunks:
            return _postprocess_pcm(b"".join(chunks), target_sample_rate)
        # Fallback to REST
        rest_pcm = await _synthesize_sarvam_rest_pcm(clean_text, target_sample_rate=target_sample_rate, language_code=language_code)
        if rest_pcm:
            return _postprocess_pcm(rest_pcm, target_sample_rate)

    # 4. Fallback to Deepgram Aura TTS if not already attempted
    if engine != "deepgram" and settings.DEEPGRAM_API_KEY:
        voice_model = getattr(settings, "DEEPGRAM_VOICE_MODEL", "aura-asteria-en")
        url = f"https://api.deepgram.com/v1/speak?model={voice_model}&encoding=linear16&sample_rate={target_sample_rate}&container=none"
        headers = {
            "Authorization": f"Token {settings.DEEPGRAM_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {"text": clean_text}
        
        for attempt in range(2):
            try:
                client = _get_http_client()
                res = await client.post(url, headers=headers, json=payload)
                if res.status_code == 200 and len(res.content) > 0:
                    cleaned = clean_pcm_audio(res.content) + (b"\x00" * 640 * 3)
                    return _postprocess_pcm(cleaned, target_sample_rate)
                else:
                    print(f"[TTSEngine] Deepgram Aura TTS fallback error ({res.status_code}): {res.text}")
                    break
            except Exception as e:
                print(f"[TTSEngine] Deepgram TTS error: {e}")
                break

    # 5. Fallback: ElevenLabs TTS if configured
    if settings.ELEVENLABS_API_KEY and settings.ELEVENLABS_API_KEY != "your_elevenlabs_api_key_here":
        try:
            client = _get_http_client()
            voice_id = settings.ELEVENLABS_VOICE_ID or "21m00Tcm4TlvDq8ikWAM"
            url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=pcm_16000"
            headers = {
                "xi-api-key": settings.ELEVENLABS_API_KEY,
                "Content-Type": "application/json"
            }
            payload = {
                "text": clean_text,
                "model_id": "eleven_monolingual_v1",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
            }
            res = await client.post(url, headers=headers, json=payload)
            if res.status_code == 200 and len(res.content) > 0:
                pcm_16k = res.content
                if target_sample_rate != 16000:
                    return _postprocess_pcm(resample_pcm_16k_to_8k(pcm_16k), target_sample_rate)
                return _postprocess_pcm(pcm_16k, target_sample_rate)
        except Exception as e:
            print(f"[TTSEngine] ElevenLabs TTS error: {e}")

    # 6. Fail-safe fallback: Edge-TTS
    pcm = await _synthesize_edge_tts_pcm(clean_text, target_sample_rate=target_sample_rate)
    if pcm:
        return _postprocess_pcm(pcm, target_sample_rate)

    return b""


async def stream_synthesize_pcm(text: str, target_sample_rate: int = 8000, language_code: Optional[str] = None):
    """
    Real-time streaming TTS generator with RAM audio caching & live office room acoustic modeling.
    Yields 16-bit linear PCM audio chunks (multiples of 640 bytes).
    Applies subtle admissions office desk presence and early reflections to transform cold synthetic TTS into a natural office call.
    Controlled by settings.TTS_ENGINE ('edge-tts', 'sarvam', 'deepgram').
    Accepts language_code to lock synthesis to caller's chosen session language.
    Pre-caches greetings and short responses (<220 chars) to eliminate API costs on repeated dialogue.
    """
    clean_text = clean_text_for_speech(text)
    if not clean_text:
        return

    engine = getattr(settings, "TTS_ENGINE", "deepgram").lower()
    speaker = getattr(settings, "SARVAM_SPEAKER", "ritu") if engine == "sarvam" else getattr(settings, "DEEPGRAM_VOICE_MODEL", "aura-asteria-en")
    use_acoustics = getattr(settings, "ENABLE_OFFICE_ACOUSTICS", False)
    wet_mix = getattr(settings, "OFFICE_ACOUSTICS_WET", 0.0)
    use_headset = getattr(settings, "ENABLE_HEADSET_OFFICE_SOUND", False)
    amb_level = getattr(settings, "OFFICE_AMBIENCE_LEVEL", 0.0) if use_headset else 0.0
    cache_key = f"{clean_text.lower().strip()}_{target_sample_rate}_{language_code or ''}_{speaker}_{engine}_{use_acoustics}_{wet_mix}_{use_headset}_{amb_level}"

    # 0. Check in-memory audio chunk cache (0ms latency, 0 API cost)
    if cache_key in _AUDIO_CHUNK_CACHE:
        _AUDIO_CHUNK_CACHE.move_to_end(cache_key)
        for chunk in _AUDIO_CHUNK_CACHE[cache_key]:
            yield chunk
        return

    can_cache = len(clean_text) <= 220
    collected_chunks: List[bytes] = []
    flt = OfficeAcousticsFilter(
        sample_rate=target_sample_rate,
        wet_mix=wet_mix,
        ambience_level=amb_level,
        headset_eq=use_headset
    ) if use_acoustics else None

    async def _raw_chunk_generator():
        # 1. Primary when deepgram: Deepgram Aura TTS (Development & Testing)
        if engine == "deepgram" and settings.DEEPGRAM_API_KEY:
            deepgram_emitted = False
            voice_model = getattr(settings, "DEEPGRAM_VOICE_MODEL", "aura-asteria-en")
            url = f"https://api.deepgram.com/v1/speak?model={voice_model}&encoding=linear16&sample_rate={target_sample_rate}&container=none"
            headers = {
                "Authorization": f"Token {settings.DEEPGRAM_API_KEY}",
                "Content-Type": "application/json"
            }
            payload = {"text": clean_text}

            for attempt in range(2):
                client = _get_http_client()
                try:
                    async with client.stream("POST", url, headers=headers, json=payload) as response:
                        if response.status_code == 200:
                            buffer = bytearray()
                            is_first_chunk = True
                            async for chunk in response.aiter_bytes():
                                buffer.extend(chunk)
                                while len(buffer) >= 640:
                                    send_chunk = bytes(buffer[:640])
                                    del buffer[:640]
                                    if is_first_chunk:
                                        send_chunk = clean_pcm_audio(send_chunk, fade_samples=20)
                                        is_first_chunk = False
                                    deepgram_emitted = True
                                    yield send_chunk
                            if buffer:
                                remainder = len(buffer) % 320
                                remainder_bytes = b"\x00" * (320 - remainder) if remainder != 0 else b""
                                final_chunk = bytes(buffer) + remainder_bytes
                                if is_first_chunk:
                                    final_chunk = clean_pcm_audio(final_chunk, fade_samples=20)
                                deepgram_emitted = True
                                yield final_chunk
                            return
                        else:
                            print(f"[TTSEngine] Deepgram streaming TTS error ({response.status_code}): {await response.aread()}")
                            break
                except Exception as e:
                    global _HTTP_CLIENT
                    if _HTTP_CLIENT is not None:
                        try:
                            await _HTTP_CLIENT.aclose()
                        except Exception:
                            pass
                        _HTTP_CLIENT = None
                    if attempt == 0:
                        continue
                    print(f"[TTSEngine] Deepgram stream exception: {e}")
            if deepgram_emitted:
                return

        # 2. Primary when edge-tts: Microsoft Neural Voice (Neerja / Swara)
        if engine in ("edge-tts", "edgetts", "microsoft"):
            async for chunk in _stream_edge_tts_pcm(clean_text, target_sample_rate=target_sample_rate):
                yield chunk
            return

        # 3. Primary when sarvam (Production) OR Indic text fallback
        if (engine == "sarvam" or (engine != "deepgram" and is_indic_text(clean_text))) and getattr(settings, "SARVAM_API_KEY", None):
            sarvam_emitted = False
            async for chunk in _stream_sarvam_ws_pcm(clean_text, target_sample_rate=target_sample_rate, language_code=language_code):
                sarvam_emitted = True
                yield chunk
            if sarvam_emitted:
                return

        # 4. Fallback to Deepgram Aura TTS if not already attempted
        if engine != "deepgram" and settings.DEEPGRAM_API_KEY:
            voice_model = getattr(settings, "DEEPGRAM_VOICE_MODEL", "aura-asteria-en")
            url = f"https://api.deepgram.com/v1/speak?model={voice_model}&encoding=linear16&sample_rate={target_sample_rate}&container=none"
            headers = {
                "Authorization": f"Token {settings.DEEPGRAM_API_KEY}",
                "Content-Type": "application/json"
            }
            payload = {"text": clean_text}

            for attempt in range(2):
                client = _get_http_client()
                try:
                    async with client.stream("POST", url, headers=headers, json=payload) as response:
                        if response.status_code == 200:
                            buffer = bytearray()
                            is_first_chunk = True
                            async for chunk in response.aiter_bytes():
                                buffer.extend(chunk)
                                while len(buffer) >= 640:
                                    send_chunk = bytes(buffer[:640])
                                    del buffer[:640]
                                    if is_first_chunk:
                                        send_chunk = clean_pcm_audio(send_chunk, fade_samples=20)
                                        is_first_chunk = False
                                    yield send_chunk
                            if buffer:
                                remainder = len(buffer) % 320
                                remainder_bytes = b"\x00" * (320 - remainder) if remainder != 0 else b""
                                final_chunk = bytes(buffer) + remainder_bytes
                                if is_first_chunk:
                                    final_chunk = clean_pcm_audio(final_chunk, fade_samples=20)
                                yield final_chunk
                            return
                        else:
                            print(f"[TTSEngine] Deepgram fallback streaming TTS error ({response.status_code}): {await response.aread()}")
                            break
                except Exception as e:
                    if attempt == 0:
                        continue
                    print(f"[TTSEngine] Deepgram fallback stream exception: {e}")

        # Fallback to non-streaming synthesize_pcm if streaming fails or other engine selected
        raw_pcm = await synthesize_pcm(clean_text, target_sample_rate=target_sample_rate)
        if raw_pcm:
            for chunk in chunk_pcm_audio(raw_pcm, chunk_size=640):
                yield chunk
            return

        # Ultimate fail-safe fallback: Microsoft Neural Voice
        async for chunk in _stream_edge_tts_pcm(clean_text, target_sample_rate=target_sample_rate):
            yield chunk

    async for raw_chunk in _raw_chunk_generator():
        processed_chunk = flt.process_chunk(raw_chunk) if flt is not None else raw_chunk
        if can_cache:
            collected_chunks.append(processed_chunk)
        yield processed_chunk

    # Room presence tail: only if office sound is explicitly enabled
    if flt is not None and getattr(settings, "ENABLE_HEADSET_OFFICE_SOUND", False):
        for _ in range(3):
            tail_chunk = flt.get_room_presence_chunk(chunk_size=640)
            if can_cache:
                collected_chunks.append(tail_chunk)
            yield tail_chunk

    if can_cache and collected_chunks:
        _AUDIO_CHUNK_CACHE[cache_key] = collected_chunks
        _AUDIO_CHUNK_CACHE.move_to_end(cache_key)
        while len(_AUDIO_CHUNK_CACHE) > MAX_TTS_CACHE_ENTRIES:
            _AUDIO_CHUNK_CACHE.popitem(last=False)


async def generate_speech(text: str, voice_name: Optional[str] = None) -> bytes:
    """
    Generates spoken audio from text for Web Call Simulator or REST endpoints.
    Priority governed by settings.TTS_ENGINE.
    """
    clean_text = clean_text_for_speech(text)
    if not clean_text:
        clean_text = "Hello from iDigitalStudies."

    engine = getattr(settings, "TTS_ENGINE", "deepgram").lower()

    # 1. Deepgram Aura TTS (Fastest & Cost-Effective for Development & Testing)
    if engine in ("deepgram", "deepgram-aura") and settings.DEEPGRAM_API_KEY:
        try:
            audio_bytes = await _generate_deepgram_speech(clean_text)
            if audio_bytes:
                return audio_bytes
        except Exception as e:
            print(f"[TTSEngine] Deepgram speech generation error: {e}")

    # 2. Sarvam AI (Production Indian Regional Voice)
    if engine == "sarvam" and getattr(settings, "SARVAM_API_KEY", None):
        try:
            audio_bytes = await _generate_sarvam_speech(clean_text)
            if audio_bytes:
                return audio_bytes
        except Exception as e:
            print(f"[TTSEngine] Sarvam TTS error: {e}. Falling back...")

    # 3. Edge-TTS if configured
    if engine in ("edge-tts", "edgetts", "microsoft"):
        selected_voice = voice_name or (
            getattr(settings, "EDGE_TTS_VOICE_HI", "hi-IN-SwaraNeural")
            if any('\u0900' <= c <= '\u097F' for c in clean_text)
            else getattr(settings, "EDGE_TTS_VOICE_EN", "en-IN-NeerjaNeural")
        )
        return await _generate_edge_tts_speech(clean_text, selected_voice)

    # Fallback to Sarvam AI if not already tried
    if engine != "sarvam" and getattr(settings, "SARVAM_API_KEY", None):
        try:
            audio_bytes = await _generate_sarvam_speech(clean_text)
            if audio_bytes:
                return audio_bytes
        except Exception:
            pass

    # Fallback to Deepgram Aura TTS if not already tried
    if engine not in ("deepgram", "deepgram-aura") and settings.DEEPGRAM_API_KEY:
        try:
            audio_bytes = await _generate_deepgram_speech(clean_text)
            if audio_bytes:
                return audio_bytes
        except Exception:
            pass

    # Try ElevenLabs next if key is configured
    if settings.ELEVENLABS_API_KEY and settings.ELEVENLABS_API_KEY != "your_elevenlabs_api_key_here":
        try:
            audio_bytes = await _generate_elevenlabs_speech(clean_text)
            if audio_bytes:
                return audio_bytes
        except Exception as e:
            print(f"[TTSEngine] ElevenLabs TTS error: {e}. Falling back to Edge-TTS.")

    # Edge-TTS Fallback (Zero cost, high quality)
    return await _generate_edge_tts_speech(clean_text, voice_name)


async def _generate_sarvam_speech(text: str) -> Optional[bytes]:
    """Generates audio bytes via Sarvam AI API for web simulator / REST endpoints."""
    speaker = getattr(settings, "SARVAM_SPEAKER", "ritu")
    model = getattr(settings, "SARVAM_MODEL", "bulbul:v3")
    lang = _detect_sarvam_language(text)
    url = "https://api.sarvam.ai/text-to-speech"
    headers = {
        "api-subscription-key": settings.SARVAM_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "inputs": [text],
        "target_language_code": lang,
        "speaker": speaker,
        "speech_sample_rate": 24000,
        "model": model
    }
    client = _get_http_client()
    res = await client.post(url, headers=headers, json=payload)
    if res.status_code == 200:
        data = res.json()
        audios = data.get("audios", [])
        if audios:
            return base64.b64decode(audios[0])
    return None


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
    
    client = _get_http_client()
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


async def _generate_deepgram_speech(text: str) -> Optional[bytes]:
    """Generates MP3 audio using Deepgram Aura TTS for testing endpoints."""
    if not settings.DEEPGRAM_API_KEY:
        return None
    voice_model = getattr(settings, "DEEPGRAM_VOICE_MODEL", "aura-asteria-en")
    url = f"https://api.deepgram.com/v1/speak?model={voice_model}"
    headers = {
        "Authorization": f"Token {settings.DEEPGRAM_API_KEY}",
        "Content-Type": "application/json"
    }
    client = _get_http_client()
    res = await client.post(url, headers=headers, json={"text": text})
    if res.status_code == 200 and len(res.content) > 0:
        return res.content
    else:
        print(f"[TTSEngine] Deepgram speech error ({res.status_code}): {res.text}")
        return None


def set_runtime_tts_engine(engine_name: str) -> str:
    """Dynamically toggle between deepgram (testing/dev) and sarvam (production) at runtime."""
    normalized = engine_name.strip().lower()
    if normalized in ("deepgram", "sarvam", "edge-tts", "elevenlabs"):
        settings.TTS_ENGINE = normalized
        _AUDIO_CHUNK_CACHE.clear()
        print(f"🔄 Switched runtime TTS Engine to: {settings.TTS_ENGINE} (cache cleared)")
        return settings.TTS_ENGINE
    raise ValueError(f"Unsupported TTS engine: {engine_name}")

