"""
Enterprise Exotel Voicebot WebSocket Stream Router
Implements:
- Full-Duplex 16-bit Linear PCM audio streaming
- Sub-second speech turnaround (< 800ms)
- Tenant resolution from dialed Indian DID (Tier-1 architecture)
- Voice Activity Detection (VAD) with instant barge-in interruption via Exotel 'clear' event
- Deepgram Aura TTS (16-bit Linear PCM) + Deepgram Nova STT
- Automatic escalation bridge & slot-filled lead capture fallback
"""
import time
import asyncio
import base64
import json
import re
import traceback
import httpx
from collections import deque
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import parse_qs

from app.core.audio_utils import pcm_rms, OfficeAcousticsFilter

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Response, Request

from app.config import settings
from app.core.auth import verify_exotel_token
from app.core.tenant_service import get_tenant_exotel_credentials
from app.core.voice_agent import (
    VoiceAgent, SESSIONS, _log_call_end, generate_executive_summary, _resolve_tenant_from_did, _resolve_did_route
)
from app.core.tts_engine import (
    synthesize_pcm, stream_synthesize_pcm, chunk_pcm_audio, clean_text_for_speech, is_tts_cached
)
from app.core.fillers import (
    should_use_filler, get_conversational_filler, strip_duplicate_filler
)
from app.core.stt_engine import transcribe_pcm
from app.core.call_monitor import CallMonitor

router = APIRouter(tags=["Exotel Voicebot Realtime"])

# Audio & VAD configuration (8000Hz, 16-bit mono Linear PCM)
FRAME_SIZE_BYTES = 320             # 20ms frame of 8kHz 16-bit PCM (160 samples * 2 bytes)
OUTBOUND_CHUNK_BYTES = 640         # 40ms chunk (multiple of 320 bytes per Exotel specification)
VAD_ENERGY_THRESHOLD = 650         # RMS threshold for caller voice in 16-bit PCM during caller turn
VAD_SPEECH_TRIGGER_FRAMES = 3      # ~60ms (3 * 20ms) of speech to begin buffering caller audio
VAD_SILENCE_TRIGGER_FRAMES = 10    # 10 * 20ms = 200ms of sustained silence to trigger instant snappy turn-end
MIN_SPEECH_BYTES = 320 * 8         # Minimum caller audio required (~160ms) to process turn

# Barge-In (Interruption) configuration while AI is speaking
# 1800 RMS triggers on deliberate vocal speech directly into the mic while rejecting line noise, mic rustle, and speaker echo
BARGE_IN_ENERGY_THRESHOLD = 1800
BARGE_IN_SPEECH_TRIGGER_FRAMES = 9   # ~180ms (9 * 20ms) of sustained human speech to interrupt AI playback
BARGE_IN_IMMUNITY_SECONDS = 0.85     # 850ms protects initial filler/echo onset, preventing self-interruption from speaker echo

_HTTP_CLIENT: Optional[httpx.AsyncClient] = None

def _get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(5.0, connect=2.0),
            limits=httpx.Limits(max_keepalive_connections=25, max_connections=50, keepalive_expiry=300.0)
        )
    return _HTTP_CLIENT


KEYTERMS = [
    "Suryansh", "Aanya", "iDigitalStudies", "counselor", "admissions",
    "placement", "fees", "demo class", "curriculum", "digital marketing",
    "scholarship", "internship", "EMI", "specialist", "executive",
    "master", "version", "versions", "syllabus", "modules", "certification",
    "offline", "online", "course", "career", "duration", "batches"
]
KEYWORDS_QUERY = "&".join([f"keywords={k}" for k in KEYTERMS])

DEEPGRAM_STT_URL = (
    f"https://api.deepgram.com/v1/listen"
    f"?model=nova-2"
    f"&language=en"
    f"&smart_format=false"
    f"&punctuate=true"
    f"&endpointing=false"
    f"&encoding=linear16"
    f"&sample_rate=8000"
    f"&{KEYWORDS_QUERY}"
)


async def warmup_streaming_clients():
    """Pre-warms persistent TLS connection to Deepgram STT."""
    if not settings.DEEPGRAM_API_KEY:
        return
    try:
        client = _get_http_client()
        headers = {
            "Authorization": f"Token {settings.DEEPGRAM_API_KEY}",
            "Content-Type": "audio/x-raw"
        }
        await client.post(DEEPGRAM_STT_URL, headers=headers, content=b"\x00\x00" * 160)
    except Exception:
        pass


async def keepalive_ping_stt():
    """Background heartbeat ping to keep Deepgram STT TLS connection hot at all times."""
    if not settings.DEEPGRAM_API_KEY:
        return
    try:
        client = _get_http_client()
        headers = {"Authorization": f"Token {settings.DEEPGRAM_API_KEY}"}
        await client.head("https://api.deepgram.com/v1/projects", headers=headers)
    except Exception:
        pass


def _split_speech_sentences(full_text: str) -> List[str]:
    """Splits response text into natural conversational sentence units for pipelined TTS."""
    clean = full_text.strip()
    raw = re.split(r'(?<=[.!?])\s+', clean)
    sentences = []
    for part in raw:
        p = part.strip()
        if not p:
            continue
        if sentences and len(p) < 15:
            sentences[-1] = sentences[-1] + " " + p
        else:
            sentences.append(p)
    return sentences if sentences else [clean]


async def transcribe_linear_pcm(audio_bytes: bytes, language_hint: Optional[str] = None) -> Tuple[str, str]:
    """Fast multilingual transcription (Sarvam saaras:v3 primary, Deepgram Nova-2 fallback)."""
    return await transcribe_pcm(audio_bytes, sample_rate=8000, language_hint=language_hint)


async def trigger_exotel_transfer_call(caller: str, escalation_number: str, virtual_did: str, tenant_id: str = "idigitalstudies") -> bool:
    """
    Transfers active call to human admissions counselor via Exotel Customer Connect API.
    Supports Hybrid Telephony: uses custom enterprise Exotel credentials if BYOC, else master pool.
    """
    creds = get_tenant_exotel_credentials(tenant_id)
    sid = creds.get("account_sid")
    api_key = creds.get("api_key")
    api_token = creds.get("api_token")
    subdomain = creds.get("subdomain", "api.exotel.com")
    caller_id = creds.get("caller_id") or virtual_did or settings.EXOTEL_CALLER_ID

    if not (sid and api_key and api_token):
        print(f"[ExotelStream] Exotel credentials not configured for live transfer (tenant={tenant_id}).")
        return False

    url = f"https://{api_key}:{api_token}@{subdomain}/v1/Accounts/{sid}/Calls/connect.json"
    data = {
        "From": caller,
        "To": escalation_number,
        "CallerId": caller_id,
        "TimeLimit": 30,
        "StatusCallback": f"{settings.BASE_URL}/exotel/transfer-callback"
    }
    try:
        client = _get_http_client()
        res = await client.post(url, data=data)
        if res.status_code in (200, 201):
            print(f"[ExotelStream] Live transfer initiated for tenant '{tenant_id}': {caller} -> {escalation_number}")
            return True
        elif res.status_code == 403 and "KYC" in res.text:
            print(f"[ExotelStream] Exotel Account KYC pending for outbound API. Exotel Inbound Flow (Passthru -> Connect applet) will bridge call directly on active line.")
            return False
        else:
            print(f"[ExotelStream] Exotel transfer API error ({res.status_code}): {res.text}")
            return False
    except Exception as e:
        print(f"[ExotelStream] Exception during Exotel transfer: {e}")
        return False


@router.websocket("/exotel/stream")
@router.websocket("/media-stream")
async def websocket_exotel_stream(websocket: WebSocket):
    """
    Full-duplex WebSocket handler for Exotel Voicebot Applet.
    Supports 16-bit Linear PCM, sub-second latency, and instant VAD barge-in.
    """
    # Parse query parameters if passed in URL
    query_params = parse_qs(str(websocket.url.query))
    if not verify_exotel_token(query_params.get("token", [""])[0]):
        await websocket.close(code=1008, reason="Invalid stream token")
        return
    await websocket.accept()
    print("[ExotelStream] Client WebSocket connected.")
    url_call_sid = query_params.get("call_sid", [""])[0] or query_params.get("CallSid", [""])[0]
    url_caller = (
        query_params.get("caller", [""])[0] 
        or query_params.get("from", [""])[0] 
        or query_params.get("From", [""])[0] 
        or "unknown"
    )
    url_called = (
        query_params.get("called", [""])[0] 
        or query_params.get("Called", [""])[0] 
        or query_params.get("to", [""])[0] 
        or query_params.get("To", [""])[0] 
        or query_params.get("CallTo", [""])[0] 
        or query_params.get("exophone", [""])[0] 
        or query_params.get("ExoPhone", [""])[0] 
        or query_params.get("DialWhomNumber", [""])[0] 
        or ""
    )
    url_direction = query_params.get("direction", ["inbound"])[0]

    stream_id: Optional[str] = None
    call_id: str = url_call_sid or f"call_{asyncio.get_event_loop().time()}"
    caller_phone: str = url_caller
    called_phone: str = url_called
    tenant_id, agent_id = _resolve_did_route(called_phone)
    agent: Optional[VoiceAgent] = None

    # State & Monitoring tracking
    call_turn_idx: int = 0
    call_start_time: float = time.perf_counter()
    call_connect_time: float = asyncio.get_event_loop().time()
    is_speaking: bool = False
    speaking_start_time: float = 0.0
    playback_generation: int = 0
    playback_task: Optional[asyncio.Task] = None
    turn_task: Optional[asyncio.Task] = None

    # VAD & Turn state
    audio_buffer: bytearray = bytearray()
    speech_frame_count: int = 0
    silence_frame_count: int = 0
    is_caller_speaking: bool = False
    consecutive_inaudible_count: int = 0

    # Barge-In rolling window & speech detection (~500ms audio buffer to capture first words of interruption)
    barge_in_frame_count: int = 0
    recent_audio_frames: deque = deque(maxlen=25)

    async def send_media_msg(chunk: bytes):
        """Direct, zero-overhead outbound telephony audio chunk sender."""
        if not stream_id or not chunk:
            return
        try:
            payload = base64.b64encode(chunk).decode("utf-8")
            media_msg = json.dumps({
                "event": "media",
                "stream_id": stream_id,
                "media": {"payload": payload}
            })
            await websocket.send_text(media_msg)
        except (WebSocketDisconnect, RuntimeError):
            # Normal socket closure by caller
            pass
        except Exception as e:
            print(f"[ExotelStream] Error sending media chunk: {e}")

    async def clear_exotel_playback():
        """Clears Exotel playback queue immediately for instant barge-in."""
        nonlocal is_speaking, playback_generation, playback_task, turn_task
        if not stream_id:
            return
        is_speaking = False
        playback_generation += 1
        if playback_task and not playback_task.done():
            playback_task.cancel()
        if turn_task and not turn_task.done():
            turn_task.cancel()
        try:
            clear_event = json.dumps({
                "event": "clear",
                "stream_id": stream_id,
                "stream_sid": stream_id,
                "streamSid": stream_id
            })
            await websocket.send_text(clear_event)
            print(f"[ExotelStream] Sent 'clear' event to Exotel (stream_id={stream_id})")
        except (WebSocketDisconnect, RuntimeError):
            pass
        except Exception as e:
            print(f"[ExotelStream] Error sending clear event: {e}")

    async def stream_pcm_chunks(pcm_bytes: bytes, gen_id: int):
        """
        Streams 16-bit Linear PCM chunks to Exotel with drift-compensated clock pacing.
        Initial burst (first 2 chunks @ 1ms) primes telephony jitter buffer, followed by uniform 38ms clock.
        """
        nonlocal is_speaking
        is_speaking = True

        chunks = chunk_pcm_audio(pcm_bytes, chunk_size=OUTBOUND_CHUNK_BYTES)
        chunk_idx = 0
        pacing_start = time.perf_counter()
        pacing_base = 0

        try:
            for chunk in chunks:
                if gen_id != playback_generation:
                    break

                chunk_idx += 1
                await send_media_msg(chunk)

                # Carrier Jitter Buffer Priming (First 2 chunks burst @ 1ms)
                if chunk_idx <= 2:
                    await asyncio.sleep(0.001)
                    pacing_start = time.perf_counter()
                    pacing_base = chunk_idx
                else:
                    target_t = pacing_start + ((chunk_idx - pacing_base) * 0.038)
                    sleep_dur = max(0.001, target_t - time.perf_counter())
                    await asyncio.sleep(sleep_dur)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[ExotelStream] Audio streaming error: {e}")
        finally:
            if gen_id == playback_generation:
                is_speaking = False

    async def speak_text(text: str, is_final: bool = False, escalate: bool = False, esc_num: str = ""):
        """
        Synthesizes text via Deepgram Aura and streams to Exotel.
        Uses Pipelined First-Sentence TTFA: synthesizes sentence 1 first and starts streaming
        immediately to the caller, while synthesising remaining sentences in the background.
        """
        nonlocal playback_generation, playback_task, is_speaking, speaking_start_time
        playback_generation += 1
        current_gen = playback_generation
        is_speaking = True
        speaking_start_time = asyncio.get_event_loop().time()

        try:
            clean_text = clean_text_for_speech(text)
            active_lang = agent.session.get("language") if agent and hasattr(agent, "session") else None
            cached = is_tts_cached(clean_text, target_sample_rate=8000, language_code=active_lang)
            if call_id in SESSIONS:
                sess_data = SESSIONS[call_id]
                sess_data["tts_characters"] = sess_data.get("tts_characters", 0) + len(clean_text)
                if cached:
                    sess_data["tts_cached_characters"] = sess_data.get("tts_cached_characters", 0) + len(clean_text)
                else:
                    sess_data["tts_billable_characters"] = sess_data.get("tts_billable_characters", 0) + len(clean_text)
                SESSIONS[call_id] = sess_data
            chunk_count = 0
            pacing_start = time.perf_counter()
            pacing_base = 0
            async for chunk in stream_synthesize_pcm(clean_text, target_sample_rate=8000, language_code=active_lang):
                if current_gen != playback_generation:
                    break
                chunk_count += 1
                await send_media_msg(chunk)

                # Carrier Jitter Buffer Priming (First 2 chunks burst @ 1ms)
                if chunk_count <= 2:
                    await asyncio.sleep(0.001)
                    pacing_start = time.perf_counter()
                    pacing_base = chunk_count
                else:
                    target_t = pacing_start + ((chunk_count - pacing_base) * 0.038)
                    sleep_dur = max(0.001, target_t - time.perf_counter())
                    await asyncio.sleep(sleep_dur)

            with open("/tmp/exotel_debug.log", "a") as f:
                f.write(f"[SPEAK_TEXT FINISHED] text='{text[:60]}...'\n")
            print("[ExotelStream] Finished streaming PCM chunks.")
        except asyncio.CancelledError:
            with open("/tmp/exotel_debug.log", "a") as f:
                f.write(f"[SPEAK_TEXT CANCELLED] Barge-in cancelled speak_text (gen={current_gen})\n")
            print(f"[ExotelStream] speak_text cancelled by barge-in (gen={current_gen})")
        except Exception as e:
            with open("/tmp/exotel_debug.log", "a") as f:
                f.write(f"[SPEAK_TEXT ERROR] Error: {e}\n{traceback.format_exc()}\n")
            print(f"[ExotelStream] Error in speak_text: {e}")
            traceback.print_exc()
        finally:
            if current_gen == playback_generation:
                is_speaking = False

        # Handle escalation if triggered
        if escalate and esc_num and current_gen == playback_generation:
            print(f"[ExotelStream] Escalation triggered -> Initiating transfer to {esc_num}")
            if agent:
                agent.mark_transferred()
            if call_id in SESSIONS:
                SESSIONS[call_id]["transfer_requested"] = True
                SESSIONS[call_id]["escalated"] = True
                SESSIONS[call_id]["escalation_number"] = esc_num
                SESSIONS[call_id]["state"] = "TRANSFERRED"
            if caller_phone:
                c_clean = caller_phone.strip()
                c_no_91 = c_clean.replace("+91", "").lstrip("0")
                for k in [c_clean, f"+91{c_no_91}", f"0{c_no_91}", c_no_91]:
                    SESSIONS[k] = SESSIONS.get(call_id, {
                        "transfer_requested": True,
                        "escalated": True,
                        "escalation_number": esc_num,
                        "state": "TRANSFERRED"
                    })
            _log_call_end(call_id, "transferred", True)

            # In Exotel Voicebot Applet, closing the WebSocket after the spoken announcement
            # automatically triggers Exotel to advance the caller to the Next Applet (Connect Applet to counselor)
            # We also launch the Exotel Customer Connect API as an automatic outbound transfer bridge
            asyncio.create_task(
                trigger_exotel_transfer_call(caller_phone, esc_num, called_phone, tenant_id)
            )
            is_speaking = True
            print(f"[ExotelStream] Escalation announcement finished -> Closing WebSocket to trigger Exotel transfer to {esc_num} for {call_id}...")
            await asyncio.sleep(1.2)
            try:
                await websocket.close(code=1000, reason=f"Escalated to counselor {esc_num}")
                print(f"[ExotelStream] WebSocket closed for escalation transfer to {esc_num} (call_id={call_id})")
            except Exception as ce:
                print(f"[ExotelStream] Error closing websocket on escalation: {ce}")
            return

        # Handle call hangup if is_final
        if is_final and current_gen == playback_generation:
            is_speaking = True
            print(f"[ExotelStream] speak_text is_final=True -> Hanging up call for {call_id}...")
            await asyncio.sleep(1.2)
            try:
                await websocket.close(code=1000, reason="Conversation completed normally")
                print(f"[ExotelStream] WebSocket closed (call ended) for {call_id}")
            except Exception as ce:
                print(f"[ExotelStream] Error closing websocket on hangup: {ce}")

    async def stream_and_play_turn(
        caller_text: str,
        turn_start_time: float = 0.0,
        stt_ms: float = 0.0,
        filler_info: Optional[Tuple[str, List[bytes]]] = None,
        turn_idx: int = 1
    ):
        """
        Ultra-low latency non-interleaved streaming pipeline:
        - Instant 0ms conversational filler acknowledgment (e.g. 'Ji bilkul!', 'Sure!')
        - AsyncGroq LLM token generation (non-blocking)
        - Background parallel sentence TTS pre-synthesis
        - Chronologically ordered sentence audio queues (zero interleaving)
        - Monotonic drift-compensated Exotel WebSocket clock pacing with 2-chunk initial jitter buffer burst
        """
        nonlocal playback_generation, playback_task, is_speaking, agent, speaking_start_time
        playback_generation += 1
        current_gen = playback_generation
        is_speaking = True
        speaking_start_time = asyncio.get_event_loop().time()

        if not agent:
            agent = VoiceAgent(call_id=call_id, tenant_id=tenant_id, caller_number=caller_phone, agent_id=agent_id)

        # Master queue holding individual sentence queues in strict chronological order
        sentence_master_queue: asyncio.Queue = asyncio.Queue()
        tts_workers = []
        t_llm_start = time.perf_counter()
        first_sentence_ms: float = 0.0
        sentence_count: int = 0
        has_filler = False
        filler_name = ""

        # 1. If conversational filler is available, immediately enqueue its audio chunks at the head of sentence_master_queue!
        if filler_info:
            filler_name, filler_chunks = filler_info
            if filler_chunks:
                has_filler = True
                if call_id in SESSIONS and filler_name:
                    sess_data = SESSIONS[call_id]
                    sess_data["tts_characters"] = sess_data.get("tts_characters", 0) + len(filler_name)
                    sess_data["tts_cached_characters"] = sess_data.get("tts_cached_characters", 0) + len(filler_name)
                    SESSIONS[call_id] = sess_data
                filler_queue = asyncio.Queue()
                for fchunk in filler_chunks:
                    await filler_queue.put(("chunk", fchunk, False, False, ""))
                
                # Insert 120ms (3 chunks of 640-byte silence) transition breathing room
                # Prevents filler and sentence #1 from crashing into each other artificially
                comfort_silence = b"\x00" * 640
                for _ in range(3):
                    await filler_queue.put(("chunk", comfort_silence, False, False, ""))

                await filler_queue.put(("sentence_end", None, False, False, ""))
                await filler_queue.put((None, None, False, False, ""))
                await sentence_master_queue.put(filler_queue)

        async def _synthesize_sentence_worker(s_index: int, s_text: str, s_final: bool, s_esc: bool, s_esc_num: str, s_queue: asyncio.Queue):
            """Synthesizes a single sentence in the background and pushes chunks to its private queue."""
            try:
                if current_gen != playback_generation:
                    return
                clean_s = clean_text_for_speech(s_text)
                if not clean_s:
                    return
                active_lang = agent.session.get("language") if agent and hasattr(agent, "session") else None
                cached = is_tts_cached(clean_s, target_sample_rate=8000, language_code=active_lang)
                if call_id in SESSIONS:
                    sess_data = SESSIONS[call_id]
                    sess_data["tts_characters"] = sess_data.get("tts_characters", 0) + len(clean_s)
                    if cached:
                        sess_data["tts_cached_characters"] = sess_data.get("tts_cached_characters", 0) + len(clean_s)
                    else:
                        sess_data["tts_billable_characters"] = sess_data.get("tts_billable_characters", 0) + len(clean_s)
                    SESSIONS[call_id] = sess_data
                t_synth_start = time.perf_counter()
                chunk_idx = 0
                async for chunk in stream_synthesize_pcm(clean_s, target_sample_rate=8000, language_code=active_lang):
                    if current_gen != playback_generation:
                        break
                    if chunk_idx == 0:
                        synth_ttfb_ms = (time.perf_counter() - t_synth_start) * 1000.0
                        CallMonitor.tts_sentence(s_index, synth_ttfb_ms)
                    await s_queue.put(("chunk", chunk, False, False, ""))
                    chunk_idx += 1

                if current_gen == playback_generation:
                    await s_queue.put(("sentence_end", None, s_final, s_esc, s_esc_num))
            except asyncio.CancelledError:
                pass
            except Exception as e:
                print(f"[ExotelStream] Synth worker error on '{s_text[:20]}': {e}")
                # If this sentence was meant to escalate, ensure escalation flag is still delivered!
                if s_esc and current_gen == playback_generation:
                    await s_queue.put(("sentence_end", None, s_final, True, s_esc_num))
            finally:
                await s_queue.put((None, None, False, False, ""))

        async def producer():
            nonlocal first_sentence_ms, sentence_count
            active_agent = agent
            if not active_agent:
                return
            try:
                # Iterate asynchronously over LLM stream
                async for item in active_agent.stream_turn(caller_text, has_filler=has_filler):
                    if current_gen != playback_generation:
                        break
                    sentence, is_final, state, escalate, esc_num = item
                    sentence_count += 1
                    
                    if sentence_count == 1:
                        first_sentence_ms = (time.perf_counter() - t_llm_start) * 1000.0
                    CallMonitor.llm_sentence(sentence_count, sentence, (time.perf_counter() - t_llm_start) * 1000.0)

                    # Create dedicated ordered queue for this sentence
                    sent_queue = asyncio.Queue()
                    await sentence_master_queue.put(sent_queue)

                    # Launch background synthesis immediately in parallel
                    worker_task = asyncio.create_task(
                        _synthesize_sentence_worker(sentence_count, sentence, is_final, escalate, esc_num, sent_queue)
                    )
                    tts_workers.append(worker_task)

                # Await all background synthesis tasks to complete
                if tts_workers:
                    await asyncio.gather(*tts_workers, return_exceptions=True)

                # If this turn asked for email/details with a beep, append the audible beep tone
                if active_agent.session.get("play_beep") and current_gen == playback_generation:
                    active_agent.session["play_beep"] = False
                    from app.core.audio_utils import get_beep_audio_8k
                    beep_queue = asyncio.Queue()
                    await sentence_master_queue.put(beep_queue)
                    await beep_queue.put(("chunk", get_beep_audio_8k(), False, False, ""))
                    await beep_queue.put(("sentence_end", None, False, False, ""))
                    await beep_queue.put((None, None, False, False, ""))

            except asyncio.CancelledError:
                pass
            except Exception as e:
                print(f"[ExotelStream Producer Error] {e}")
            finally:
                # Fail-safe check: If no sentences were queued (e.g. LLM timeout/rate-limit or error), play graceful transfer apology
                if sentence_count == 0 and current_gen == playback_generation:
                    try:
                        from app.core.tts_engine import get_graceful_transfer_message
                        from app.core.tenant_service import get_tenant_escalation_target
                        active_lang = agent.session.get("language", "hi-IN") if agent and hasattr(agent, "session") else "hi-IN"
                        esc_num, _ = get_tenant_escalation_target(tenant_id, caller_text)
                        fallback_msg = get_graceful_transfer_message(active_lang)
                        print(f"[ExotelStream] 🛡️ Fail-safe activated: Zero sentences produced -> Queueing graceful apology and escalation to {esc_num}")

                        sent_queue = asyncio.Queue()
                        await sentence_master_queue.put(sent_queue)
                        sentence_count += 1

                        fb_task = asyncio.create_task(
                            _synthesize_sentence_worker(sentence_count, fallback_msg, False, True, esc_num, sent_queue)
                        )
                        tts_workers.append(fb_task)
                        await fb_task
                    except Exception as fe:
                        print(f"[ExotelStream] Error during fail-safe synthesis: {fe}")

                for w in tts_workers:
                    if not w.done():
                        w.cancel()
                await sentence_master_queue.put(None)

        async def consumer():
            nonlocal is_speaking
            should_hangup = False
            should_escalate_transfer = False
            target_esc_num = ""
            first_chunk_delivered = False
            chunk_idx = 0
            pacing_start = time.perf_counter()
            pacing_base = 0

            try:
                while True:
                    sent_queue = await sentence_master_queue.get()
                    if sent_queue is None or current_gen != playback_generation:
                        break

                    # Drain this sentence queue completely before moving to next sentence
                    while True:
                        item = await sent_queue.get()
                        kind, chunk_bytes, is_final, escalate, esc_num = item
                        if kind is None or current_gen != playback_generation:
                            break

                        if kind == "chunk" and chunk_bytes and current_gen == playback_generation:
                            chunk_idx += 1
                            if not first_chunk_delivered and turn_start_time > 0:
                                first_chunk_delivered = True
                                total_turnaround_ms = (time.perf_counter() - turn_start_time) * 1000.0
                                CallMonitor.turn_timeline(
                                    turn_idx=turn_idx,
                                    vad_ms=200.0,
                                    stt_ms=stt_ms,
                                    first_audio_ms=total_turnaround_ms,
                                    total_audio_chunks=chunk_idx,
                                    has_filler=has_filler,
                                    filler_name=filler_name,
                                    is_cache_hit=False
                                )

                            await send_media_msg(chunk_bytes)

                            # Carrier Jitter Buffer Priming (First 2 chunks burst @ 1ms)
                            if chunk_idx <= 2:
                                await asyncio.sleep(0.001)
                                pacing_start = time.perf_counter()
                                pacing_base = chunk_idx
                            else:
                                target_t = pacing_start + ((chunk_idx - pacing_base) * 0.038)
                                sleep_dur = max(0.001, target_t - time.perf_counter())
                                await asyncio.sleep(sleep_dur)

                        elif kind == "sentence_end" and current_gen == playback_generation:
                            if escalate and esc_num:
                                print(f"[ExotelStream] Consumer: Escalation triggered -> Transferring to {esc_num}")
                                if agent:
                                    agent.mark_transferred()
                                if call_id in SESSIONS:
                                    SESSIONS[call_id]["transfer_requested"] = True
                                    SESSIONS[call_id]["escalated"] = True
                                    SESSIONS[call_id]["escalation_number"] = esc_num
                                    SESSIONS[call_id]["state"] = "TRANSFERRED"
                                if caller_phone:
                                    c_clean = caller_phone.strip()
                                    c_no_91 = c_clean.replace("+91", "").lstrip("0")
                                    for k in [c_clean, f"+91{c_no_91}", f"0{c_no_91}", c_no_91]:
                                        SESSIONS[k] = SESSIONS.get(call_id, {
                                            "transfer_requested": True,
                                            "escalated": True,
                                            "escalation_number": esc_num,
                                            "state": "TRANSFERRED"
                                        })
                                _log_call_end(call_id, "transferred", True)
                                should_escalate_transfer = True
                                target_esc_num = esc_num

                            if is_final:
                                should_hangup = True

            except asyncio.CancelledError:
                pass
            except Exception as e:
                print(f"[ExotelStream Consumer Error] {e}")
            finally:
                if should_escalate_transfer and current_gen == playback_generation:
                    is_speaking = True
                    print(f"[ExotelStream] Escalation announcement finished -> Closing WebSocket to trigger Exotel transfer to {target_esc_num} for {call_id}...")
                    asyncio.create_task(
                        trigger_exotel_transfer_call(caller_phone, target_esc_num, called_phone, tenant_id)
                    )
                    await asyncio.sleep(1.2)
                    try:
                        await websocket.close(code=1000, reason=f"Escalated to counselor {target_esc_num}")
                        print(f"[ExotelStream] WebSocket closed for escalation (call_id={call_id})")
                    except Exception as ce:
                        print(f"[ExotelStream] Error closing websocket on escalation: {ce}")
                elif should_hangup and current_gen == playback_generation:
                    is_speaking = True
                    print(f"[ExotelStream] Final farewell finished -> Hanging up call for {call_id}...")
                    await asyncio.sleep(1.2)
                    try:
                        await websocket.close(code=1000, reason="Conversation completed normally")
                        print(f"[ExotelStream] WebSocket closed (call ended) for {call_id}")
                    except Exception as ce:
                        print(f"[ExotelStream] Error closing websocket on hangup: {ce}")
                elif current_gen == playback_generation:
                    is_speaking = False

        async def _run_stream_playback():
            await asyncio.gather(producer(), consumer())

        playback_task = asyncio.create_task(_run_stream_playback())
        try:
            await playback_task
        except asyncio.CancelledError:
            print(f"[ExotelStream] stream_and_play_turn cancelled by barge-in (gen={current_gen})")
            if playback_task and not playback_task.done():
                playback_task.cancel()
            for w in tts_workers:
                if not w.done():
                    w.cancel()

    max_call_sec: float = float(settings.MAX_CALL_DURATION_SECONDS)

    # Main WebSocket Loop
    try:
        while True:
            message = await websocket.receive_text()
            if time.perf_counter() - call_start_time > max_call_sec:
                print(f"[ExotelStream] ⏱️ Call {call_id} hit prepaid balance/duration limit ({max_call_sec}s); hanging up.")
                await websocket.close(code=1000, reason="Prepaid balance or duration limit reached")
                break
            try:
                data = json.loads(message)
            except (json.JSONDecodeError, TypeError) as jde:
                print(f"[ExotelStream] Skipping malformed non-JSON frame: {jde}")
                continue
            event = data.get("event")

            # 1. START EVENT (Exotel Call Connected)
            if event == "start":
                start_meta = data.get("start", {})
                stream_id = (
                    data.get("stream_sid") 
                    or data.get("stream_id") 
                    or data.get("streamSid") 
                    or start_meta.get("stream_sid") 
                    or start_meta.get("streamSid") 
                    or start_meta.get("stream_id") 
                    or "stream_default"
                )
                extracted_call_sid = (
                    data.get("call_sid")
                    or data.get("callSid")
                    or data.get("CallSid")
                    or start_meta.get("call_sid")
                    or start_meta.get("callSid")
                    or start_meta.get("CallSid")
                    or data.get("call_id")
                    or start_meta.get("call_id")
                    or url_call_sid
                )
                if extracted_call_sid:
                    call_id = extracted_call_sid
                custom_params = (
                    start_meta.get("custom_parameters") 
                    or start_meta.get("customParameters") 
                    or data.get("custom_parameters") 
                    or {}
                )
                caller_phone = (
                    custom_params.get("caller")
                    or custom_params.get("from")
                    or custom_params.get("Caller")
                    or custom_params.get("From")
                    or data.get("from") 
                    or data.get("From") 
                    or start_meta.get("from") 
                    or start_meta.get("From") 
                    or (url_caller if url_caller and url_caller != "unknown" else "")
                    or caller_phone
                )
                called_phone = (
                    custom_params.get("called")
                    or custom_params.get("to")
                    or custom_params.get("Called")
                    or custom_params.get("To")
                    or url_called
                    or data.get("called") 
                    or data.get("Called") 
                    or data.get("exophone") 
                    or data.get("ExoPhone") 
                    or data.get("to") 
                    or data.get("To") 
                    or start_meta.get("called") 
                    or start_meta.get("Called") 
                    or start_meta.get("to") 
                    or start_meta.get("To") 
                    or called_phone
                )

                # Tier-1 Tenant & Agent Resolution
                # Unknown DID with no DEFAULT_TENANT_ID yields "", which the wallet check below rejects
                tenant_id, agent_id = _resolve_did_route(called_phone)

                # Financial Guardrail: Verify prepaid wallet balance before processing call
                from app.services.tenant.tenant_service import check_tenant_wallet_balance
                bal_ok, curr_bal, bal_msg = check_tenant_wallet_balance(tenant_id, min_required=10.0)
                if not bal_ok:
                    print(f"[ExotelStream] 🛑 Inbound call rejected for {tenant_id}: {bal_msg}")
                    rejection_msg = "Kshama kijiye, is line par baat karne ke liye paryapt credit uplabdh nahi hai. Kripya apne administrator se sampark karein."
                    playback_task = asyncio.create_task(speak_text(rejection_msg, is_final=True))
                    break

                # Dynamic maximum duration bounded by prepaid balance
                max_call_sec = min(float(settings.MAX_CALL_DURATION_SECONDS), max(60.0, (curr_bal / 5.0) * 60.0))

                try:
                    agent = VoiceAgent(call_id=call_id, tenant_id=tenant_id, caller_number=caller_phone, agent_id=agent_id)
                    greeting = agent.get_greeting()
                except Exception as ge:
                    print(f"[ExotelStream] Error initializing agent or greeting: {ge}")
                    greeting = "Namaste! Hamari service mein aapka swagat hai. Main aapki kya madad kar sakti hoon?"

                CallMonitor.greeting(call_id, greeting)

                # Speak greeting immediately with fail-safe recovery
                try:
                    playback_task = asyncio.create_task(speak_text(greeting))
                except Exception as pe:
                    print(f"[ExotelStream] Error starting greeting playback: {pe}")

            # 2. MEDIA EVENT (Caller 16-bit PCM Audio Chunk)
            elif event == "media":
                media_payload = data.get("media", {}).get("payload", "")
                if not media_payload:
                    continue

                raw_bytes = base64.b64decode(media_payload)
                if not raw_bytes:
                    continue

                # Voice Activity Detection (VAD) on 16-bit PCM
                energy = pcm_rms(raw_bytes, 2)

                # -----------------------------------------------------------------
                # A. BARGE-IN INTERRUPTION DETECTION (When AI is speaking / playing audio)
                # -----------------------------------------------------------------
                if is_speaking:
                    recent_audio_frames.append(raw_bytes)
                    turn_speech_elapsed = asyncio.get_event_loop().time() - speaking_start_time
                    call_elapsed = asyncio.get_event_loop().time() - call_connect_time
                    can_barge_in = (call_elapsed > 1.2) and (turn_speech_elapsed > BARGE_IN_IMMUNITY_SECONDS)

                    if can_barge_in and energy >= BARGE_IN_ENERGY_THRESHOLD:
                        barge_in_frame_count += 1
                        if barge_in_frame_count >= BARGE_IN_SPEECH_TRIGGER_FRAMES:
                            CallMonitor.barge_in(call_id, energy, BARGE_IN_ENERGY_THRESHOLD)
                            # 1. Clear Exotel audio buffer and cancel ongoing playback tasks
                            await clear_exotel_playback()

                            # 2. Switch state immediately to caller speaking
                            is_caller_speaking = True
                            speech_frame_count = barge_in_frame_count
                            silence_frame_count = 0
                            barge_in_frame_count = 0

                            # 3. Pre-seed audio_buffer with the initial speech frames captured in rolling window
                            audio_buffer.clear()
                            for chunk in recent_audio_frames:
                                audio_buffer.extend(chunk)
                            recent_audio_frames.clear()
                    else:
                        # Smooth decay: allow natural syllable micro-pauses without instantly wiping out the counter
                        if barge_in_frame_count > 0:
                            barge_in_frame_count = max(0, barge_in_frame_count - 1)

                    # While agent is speaking and barge-in hasn't triggered, skip further processing
                    if is_speaking:
                        continue

                # -----------------------------------------------------------------
                # B. CALLER TURN VOICE ACTIVITY DETECTION (VAD)
                # -----------------------------------------------------------------
                barge_in_frame_count = 0
                recent_audio_frames.append(raw_bytes)

                if energy >= VAD_ENERGY_THRESHOLD:
                    speech_frame_count += 1
                    silence_frame_count = 0

                    if not is_caller_speaking and speech_frame_count >= VAD_SPEECH_TRIGGER_FRAMES:
                        is_caller_speaking = True
                        # Prepend recent frames before trigger so the first syllable is not clipped
                        if not audio_buffer and recent_audio_frames:
                            for chunk in list(recent_audio_frames)[:-1]:
                                audio_buffer.extend(chunk)
                else:
                    silence_frame_count += 1
                    if silence_frame_count > 2:
                        speech_frame_count = 0

                # -----------------------------------------------------------------
                # C. BUFFER AUDIO WHEN CALLER SPEAKS
                # -----------------------------------------------------------------
                if is_caller_speaking:
                    audio_buffer.extend(raw_bytes)

                    # D. TURN-END DETECTION (Caller paused/stopped speaking)
                    # Adaptive Silence Threshold: allow 1.4s (70 frames) of breathing room when dictating email/phone
                    sess_check = SESSIONS.get(call_id, {})
                    b_ctx = sess_check.get("brochure_context", {})
                    is_dictating = bool(b_ctx.get("active") and b_ctx.get("step") in ("ask_email", "ask_whatsapp_number"))
                    effective_silence_frames = 70 if is_dictating else VAD_SILENCE_TRIGGER_FRAMES

                    if silence_frame_count >= effective_silence_frames and len(audio_buffer) >= MIN_SPEECH_BYTES:
                        is_caller_speaking = False
                        speech_frame_count = 0
                        silence_frame_count = 0

                        caller_audio = bytes(audio_buffer)
                        audio_buffer.clear()
                        recent_audio_frames.clear()

                        is_speaking = True  # Prevent incoming packets from triggering VAD while processing & synthesizing

                        turn_start_time = time.perf_counter()
                        t_stt_0 = time.perf_counter()
                        lang_hint = agent.session.get("language", "en-IN") if agent and hasattr(agent, "session") and agent.session else "en-IN"
                        user_text, detected_lang = await transcribe_linear_pcm(caller_audio, language_hint=lang_hint)
                        stt_elapsed_ms = (time.perf_counter() - t_stt_0) * 1000.0
                        if call_id in SESSIONS:
                            audio_dur = round(len(caller_audio) / 16000.0, 2)
                            SESSIONS[call_id]["stt_audio_seconds"] = SESSIONS[call_id].get("stt_audio_seconds", 0.0) + audio_dur
                            if user_text:
                                SESSIONS[call_id]["stt_characters"] = SESSIONS[call_id].get("stt_characters", 0) + len(user_text)

                        if not user_text or len(user_text.strip()) <= 1:
                            consecutive_inaudible_count += 1
                            active_lang = agent.session.get("language", "hi-IN") if agent and hasattr(agent, "session") else "hi-IN"
                            if consecutive_inaudible_count == 1:
                                from app.core.tts_engine import get_inaudible_prompt_message
                                prompt_text = get_inaudible_prompt_message(active_lang)
                                playback_task = asyncio.create_task(speak_text(prompt_text))
                            elif consecutive_inaudible_count >= 2:
                                from app.core.tts_engine import get_graceful_transfer_message
                                from app.core.tenant_service import get_tenant_escalation_target
                                esc_num, _ = get_tenant_escalation_target(tenant_id)
                                transfer_msg = get_graceful_transfer_message(active_lang)
                                playback_task = asyncio.create_task(speak_text(transfer_msg, escalate=True, esc_num=esc_num))
                            else:
                                is_speaking = False
                            continue

                        consecutive_inaudible_count = 0

                        call_turn_idx += 1
                        CallMonitor.stt_result(call_turn_idx, user_text, stt_elapsed_ms)

                        if not agent:
                            agent = VoiceAgent(call_id=call_id, tenant_id=tenant_id, caller_number=caller_phone, agent_id=agent_id)

                        if agent and hasattr(agent, "session") and agent.session:
                            from app.core.voice_agent import detect_language_switch_intent
                            switch_intent = detect_language_switch_intent(user_text, tenant_id=agent.tenant_id, config=agent.config)
                            if switch_intent:
                                agent.session["language"] = switch_intent[0]
                            elif any(k in user_text.lower() for k in ["speak in english", "speak english", "in english", "english please", "switch to english"]):
                                agent.session["language"] = "en-IN"
                            elif detected_lang:
                                neutral_words = {"hello", "hi", "hey", "ok", "okay", "yes", "yeah", "yep", "hmm"}
                                if user_text.lower().strip() in neutral_words and agent.session.get("language"):
                                    pass
                                else:
                                    agent.session["language"] = detected_lang
                            elif not agent.session.get("language"):
                                agent.session["language"] = detected_lang or "en-IN"

                        # Check if caller utterance warrants an immediate conversational filler (0ms perceived latency)
                        filler_info = None
                        if should_use_filler(user_text, getattr(agent, "session", None)):
                            filler_info = get_conversational_filler(user_text, getattr(agent, "session", None))

                        CallMonitor.filler_event(
                            bool(filler_info),
                            filler_info[0] if filler_info else None,
                            len(filler_info[1]) if filler_info else 0
                        )

                        # Process Turn with real-time token streaming + parallel TTS synthesis + instant filler
                        turn_task = asyncio.create_task(
                            stream_and_play_turn(
                                user_text,
                                turn_start_time=turn_start_time,
                                stt_ms=stt_elapsed_ms,
                                filler_info=filler_info,
                                turn_idx=call_turn_idx
                            )
                        )

            # 3. DTMF EVENT
            elif event == "dtmf":
                digit = data.get("dtmf", {}).get("digit")
                if digit == "0":
                    # Instant transfer request via keypad
                    esc_num = settings.DEFAULT_ESCALATION_NUMBER
                    playback_task = asyncio.create_task(speak_text("Connecting you to our admissions counselor.", escalate=True, esc_num=esc_num))

            # 4. STOP EVENT (Call Ended)
            elif event == "stop":
                break

    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception as e:
        print(f"[ExotelStream Error] {e}")
        CallMonitor.error(call_id, "ExotelStream", f"Unhandled WebSocket loop exception: {e}", exc=e)
    finally:
        try:
            if playback_task and not playback_task.done():
                playback_task.cancel()
        except Exception:
            pass
        try:
            if turn_task and not turn_task.done():
                turn_task.cancel()
        except Exception:
            pass
        if call_id in SESSIONS:
            sess = SESSIONS[call_id]
            is_transferred = sess.get("transfer_requested", False) or sess.get("transfer_accepted", False) or sess.get("state") == "TRANSFERRED"
            outcome = "transferred" if is_transferred else ("completed" if sess.get("state") == "COMPLETED" else "ended")
            try:
                _log_call_end(call_id, outcome, is_transferred)
            except Exception as le:
                print(f"[ExotelStream] Error in _log_call_end: {le}")
            dur = time.perf_counter() - call_start_time if call_start_time > 0 else 0.0
            try:
                CallMonitor.call_ended(call_id, dur, outcome, is_transferred, esc_num=sess.get("escalation_number", ""))
            except Exception as me:
                print(f"[ExotelStream] Error in CallMonitor.call_ended: {me}")
            try:
                generate_executive_summary(call_id, outcome=outcome)
            except Exception as se:
                print(f"[ExotelStream] Error generating executive summary: {se}")
