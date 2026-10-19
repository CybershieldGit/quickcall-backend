"""
Asterisk WebRTC / SIP Audio Stream Router for QwickCall
Implements:
- Full-Duplex 16-bit Linear PCM audio streaming over WebSocket
- Sub-second speech turnaround (< 800ms) with Deepgram Nova + Groq LLM + Sarvam AI / Deepgram Aura TTS
- Voice Activity Detection (VAD) with instant barge-in interruption
- PostgreSQL call session, duration, transcript, and summary telemetry
"""
import asyncio
import base64
import json
import time
import traceback
import httpx
from collections import deque
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import parse_qs

from app.core.audio_utils import pcm_rms, OfficeAcousticsFilter

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import settings
from fastapi import Depends
from app.core.auth import verify_internal_bridge_token, get_current_admin
from app.core.tenant_service import resolve_tenant_from_did, resolve_did_route, load_tenant_config
from app.core.voice_agent import (
    VoiceAgent, SESSIONS, _log_call_end, generate_executive_summary
)
from app.core.tts_engine import (
    chunk_pcm_audio, stream_synthesize_pcm, clean_text_for_speech, is_tts_cached
)
from app.core.fillers import (
    should_use_filler, get_conversational_filler, strip_duplicate_filler
)
from app.core.stt_engine import transcribe_pcm

router = APIRouter(tags=["Asterisk WebRTC SIP Stream"])

# Audio & VAD configuration (8000Hz, 16-bit mono Linear PCM)
FRAME_SIZE_BYTES = 320             # 20ms frame of 8kHz 16-bit PCM (160 samples * 2 bytes)
OUTBOUND_CHUNK_BYTES = 640         # 40ms chunk
VAD_ENERGY_THRESHOLD = 950         # Calibrated above telephony line noise (~300-800 RMS) for human speech (>1000 RMS)
VAD_SPEECH_TRIGGER_FRAMES = 5      # ~100ms of sustained speech to begin buffering caller audio
VAD_SILENCE_TRIGGER_FRAMES = 21    # ~420ms (21 * 20ms) of sustained silence for snappy, sub-500ms conversational turn-taking
MIN_SPEECH_BYTES = 320 * 8         # Minimum caller audio required (~160ms) to process turn

# Barge-In (Interruption) configuration while AI is speaking
# 2000 RMS allows clear caller interruption (~2200-3500 RMS normal speech)
# while rejecting line background noise (~300-800 RMS) and mobile speaker acoustic bleed
BARGE_IN_ENERGY_THRESHOLD = 2000
BARGE_IN_SPEECH_TRIGGER_FRAMES = 9   # ~180ms (9 * 20ms) of sustained human speech to interrupt AI playback
BARGE_IN_IMMUNITY_SECONDS = 0.85     # 850ms protects initial filler/echo onset and prevents acoustic speaker feedback loops

_HTTP_CLIENT: Optional[httpx.AsyncClient] = None

def _get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(4.0, connect=1.5),
            limits=httpx.Limits(max_keepalive_connections=25, max_connections=50, keepalive_expiry=15.0)
        )
    return _HTTP_CLIENT


async def transcribe_audio_pcm(pcm_data: bytes, sample_rate: int = 8000, language_hint: Optional[str] = None, is_dictating: bool = False) -> Tuple[str, str]:
    """Fast multilingual transcription (Deepgram Nova-2 prioritized for alphanumeric dictation, Sarvam saaras:v3 for conversational speech)."""
    return await transcribe_pcm(pcm_data, sample_rate=sample_rate, language_hint=language_hint, is_dictating=is_dictating)


async def warmup_sip_stt():
    """Pre-warms persistent TLS socket to Deepgram STT for SIP calls."""
    if not settings.DEEPGRAM_API_KEY:
        return
    try:
        client = _get_http_client()
        headers = {
            "Authorization": f"Token {settings.DEEPGRAM_API_KEY}",
            "Content-Type": "audio/raw"
        }
        url = "https://api.deepgram.com/v1/listen?model=nova-2&language=en&encoding=linear16&sample_rate=8000"
        await client.post(url, headers=headers, content=b"\x00\x00" * 160)
    except Exception:
        pass


async def keepalive_ping_sip_stt():
    """Background heartbeat ping to keep SIP Deepgram STT TLS connection hot."""
    if not settings.DEEPGRAM_API_KEY:
        return
    try:
        client = _get_http_client()
        headers = {"Authorization": f"Token {settings.DEEPGRAM_API_KEY}"}
        await client.head("https://api.deepgram.com/v1/projects", headers=headers)
    except Exception:
        pass


@router.websocket("/sip/stream")
async def sip_webrtc_stream_handler(websocket: WebSocket):
    """
    Full-duplex WebSocket handler for Asterisk WebRTC SIP audio stream.
    Receives caller audio, runs VAD & Deepgram STT, generates response with VoiceAgent,
    and streams synthesized speech back to the caller in real-time.
    """
    query_params = parse_qs(str(websocket.url.query))
    # Loopback bridge only: the in-process SIP client presents an HMAC-derived token
    if not verify_internal_bridge_token(query_params.get("bridge_token", [""])[0]):
        await websocket.close(code=1008, reason="Invalid bridge token")
        return
    await websocket.accept()
    print("[SIPStream] Client WebSocket connected.")
    sip_call_start = time.perf_counter()

    url_call_id = query_params.get("call_id", [""])[0]
    url_caller = query_params.get("caller", [""])[0] or "unknown"
    url_called = query_params.get("called", [""])[0] or ""
    url_tenant = query_params.get("tenant_id", [""])[0] or ""

    stream_id = f"sip_stream_{asyncio.get_event_loop().time()}"
    call_id: str = url_call_id or f"sip_{asyncio.get_event_loop().time()}"
    caller_phone: str = url_caller
    called_phone: str = url_called
    tid, aid = resolve_did_route(called_phone) if called_phone else resolve_did_route(caller_phone)
    tenant_id: str = url_tenant or tid
    agent_id: Optional[str] = aid
    agent: Optional[VoiceAgent] = None

    # State tracking
    is_speaking: bool = False
    playback_generation: int = 0
    playback_task: Optional[asyncio.Future[Any]] = None
    turn_task: Optional[asyncio.Task] = None
    call_connect_time: float = asyncio.get_event_loop().time()

    # VAD & Turn state
    audio_buffer: bytearray = bytearray()
    speech_frame_count: int = 0
    silence_frame_count: int = 0
    is_caller_speaking: bool = False
    consecutive_inaudible_count: int = 0
    speaking_start_time: float = 0.0

    # Barge-In rolling window & speech detection (~500ms audio buffer to capture first words of interruption)
    barge_in_frame_count: int = 0
    recent_audio_frames: deque = deque(maxlen=25)

    async def send_media_msg(chunk: bytes):
        """Direct, zero-overhead outbound telephony audio chunk sender."""
        if not chunk:
            return
        try:
            payload = base64.b64encode(chunk).decode("utf-8")
            await websocket.send_text(json.dumps({
                "event": "media",
                "media": {"payload": payload}
            }))
        except (WebSocketDisconnect, RuntimeError):
            pass
        except Exception as e:
            print(f"[SIPStream] Error sending media chunk: {e}")

    async def clear_playback():
        """Clears playback queue immediately for instant barge-in."""
        nonlocal is_speaking, playback_generation, playback_task, turn_task
        is_speaking = False
        playback_generation += 1
        if playback_task and not playback_task.done():
            playback_task.cancel()
        if turn_task and not turn_task.done():
            turn_task.cancel()
        try:
            await websocket.send_text(json.dumps({"event": "clear"}))
        except Exception:
            pass

    async def speak_text(text: str, is_final: bool = False, is_escalation: bool = False, esc_num: str = ""):
        """Synthesizes text via Sarvam AI / Deepgram Aura and streams chunks to caller."""
        nonlocal playback_generation, playback_task, is_speaking, speaking_start_time
        playback_generation += 1
        current_gen = playback_generation
        is_speaking = True
        speaking_start_time = asyncio.get_event_loop().time()

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
        print(f"[SIPStream] Synthesizing speech ({len(clean_text)} chars, cached={cached}, lang={active_lang}): '{clean_text[:60]}...'")
        pacing_start = time.perf_counter()
        pacing_base = 0
        chunk_idx = 0
        try:
            async for chunk in stream_synthesize_pcm(clean_text, target_sample_rate=8000, language_code=active_lang):
                if current_gen != playback_generation:
                    break
                chunk_idx += 1
                await send_media_msg(chunk)
                if chunk_idx <= 2:
                    await asyncio.sleep(0.001)
                    pacing_start = time.perf_counter()
                    pacing_base = chunk_idx
                else:
                    target_t = pacing_start + ((chunk_idx - pacing_base) * 0.038)
                    sleep_dur = max(0.001, target_t - time.perf_counter())
                    await asyncio.sleep(sleep_dur)
        except asyncio.CancelledError:
            print(f"[SIPStream] Speech playback cancelled by barge-in")
        except Exception as e:
            print(f"[SIPStream] Error in speech playback: {e}")
        finally:
            if current_gen == playback_generation:
                # Keep is_speaking True for 400ms hangover to let final WebRTC frames play out cleanly
                await asyncio.sleep(0.4)
                if current_gen == playback_generation:
                    is_speaking = False

        if is_escalation and esc_num and current_gen == playback_generation:
            print(f"[SIPStream] 📞 Signaling transfer to {esc_num} for call {call_id}...")
            try:
                await websocket.send_text(json.dumps({
                    "event": "transfer",
                    "target_number": esc_num
                }))
            except Exception as te:
                print(f"[SIPStream] Error signaling transfer: {te}")

        if is_final and current_gen == playback_generation:
            try:
                await websocket.send_text(json.dumps({"event": "hangup"}))
            except Exception:
                pass
            await asyncio.sleep(1.5)
            try:
                await websocket.close(code=1000, reason="Completed normally")
            except Exception:
                pass

    async def process_caller_turn(caller_text: str):
        """
        Pipelined sentence-streaming processing for sub-400ms turnaround time:
        - 0ms instant conversational filler playback (if applicable)
        - Groq sentence-level streaming generator (first sentence in ~180ms)
        - Concurrent TTS synthesis per sentence
        - Immediate audio streaming to Asterisk/SIP caller
        """
        nonlocal agent, is_speaking, playback_task, playback_generation, speaking_start_time
        if not agent or not caller_text.strip():
            return
        print(f"[SIPStream] Caller Said: '{caller_text}'")

        playback_generation += 1
        current_gen = playback_generation
        is_speaking = True
        speaking_start_time = asyncio.get_event_loop().time()

        filler_info = None
        if should_use_filler(caller_text, getattr(agent, "session", None)):
            filler_info = get_conversational_filler(caller_text, getattr(agent, "session", None))

        has_filler = bool(filler_info)
        filler_word = filler_info[0] if filler_info else None
        filler_chunks = filler_info[1] if filler_info else []

        if has_filler:
            print(f"[SIPStream] ⚡ Immediate conversational filler triggered: '{filler_word}' ({len(filler_chunks)} chunks, 0ms latency)")
            if call_id in SESSIONS and filler_word:
                sess_data = SESSIONS[call_id]
                sess_data["tts_characters"] = sess_data.get("tts_characters", 0) + len(filler_word)
                sess_data["tts_cached_characters"] = sess_data.get("tts_cached_characters", 0) + len(filler_word)
                SESSIONS[call_id] = sess_data

        # Master queue receives ordered sentence queues
        sentence_master_queue: asyncio.Queue = asyncio.Queue()
        tts_workers = []

        # If filler chunks exist, insert them as the first queue
        if filler_chunks:
            filler_q = asyncio.Queue()
            for chunk in filler_chunks:
                await filler_q.put(chunk)
            # 120ms breathing pause between filler and answer
            for _ in range(3):
                await filler_q.put(b"\x00" * 640)
            await filler_q.put(None)
            await sentence_master_queue.put(filler_q)

        async def _synth_sentence_worker(s_text: str, is_final: bool, s_queue: asyncio.Queue, is_escalation: bool = False, esc_num: str = ""):
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
                print(f"[SIPStream] Synthesizing sentence ({len(clean_s)} chars, cached={cached}, lang={active_lang}): '{clean_s[:50]}...'")
                async for chunk in stream_synthesize_pcm(clean_s, target_sample_rate=8000, language_code=active_lang):
                    if current_gen != playback_generation:
                        break
                    await s_queue.put(chunk)
                await s_queue.put(("end", is_final, is_escalation, esc_num))
            except Exception as e:
                print(f"[SIPStream] Sentence synth error on '{s_text[:20]}': {e}")
                if is_escalation and current_gen == playback_generation:
                    await s_queue.put(("end", is_final, True, esc_num))
            finally:
                await s_queue.put(None)

        async def producer():
            if not agent:
                return
            sentence_count = 0
            try:
                async for item in agent.stream_turn(caller_text, has_filler=has_filler):
                    if current_gen != playback_generation:
                        break
                    sentence, is_final, state, escalate, esc_num = item
                    if not sentence or not sentence.strip():
                        continue
                    sentence_count += 1
                    s_queue = asyncio.Queue()
                    await sentence_master_queue.put(s_queue)
                    w_task = asyncio.create_task(_synth_sentence_worker(sentence, is_final, s_queue, is_escalation=escalate, esc_num=esc_num))
                    tts_workers.append(w_task)
                if tts_workers:
                    await asyncio.gather(*tts_workers, return_exceptions=True)

                # If this turn asked for email/details with a beep, append the audible beep tone
                if agent and hasattr(agent, "session") and agent.session.get("play_beep") and current_gen == playback_generation:
                    agent.session["play_beep"] = False
                    from app.core.audio_utils import get_beep_audio_8k
                    beep_queue = asyncio.Queue()
                    await sentence_master_queue.put(beep_queue)
                    await beep_queue.put(get_beep_audio_8k())
                    await beep_queue.put(None)
            except Exception as e:
                print(f"[SIPStream Producer Error] {e}")
            finally:
                # Fail-safe check: If no sentences were queued (e.g. LLM timeout or error), play graceful transfer apology
                if sentence_count == 0 and current_gen == playback_generation:
                    try:
                        from app.core.tts_engine import get_graceful_transfer_message
                        from app.core.tenant_service import get_tenant_escalation_target
                        active_lang = agent.session.get("language", "hi-IN") if agent and hasattr(agent, "session") else "hi-IN"
                        esc_num, _ = get_tenant_escalation_target(tenant_id, caller_text)
                        fallback_msg = get_graceful_transfer_message(active_lang)
                        print(f"[SIPStream] 🛡️ Fail-safe activated: Zero sentences produced -> Queueing graceful apology and escalation to {esc_num}")

                        s_queue = asyncio.Queue()
                        await sentence_master_queue.put(s_queue)
                        sentence_count += 1
                        fb_task = asyncio.create_task(_synth_sentence_worker(fallback_msg, False, s_queue, is_escalation=True, esc_num=esc_num))
                        tts_workers.append(fb_task)
                        await fb_task
                    except Exception as fe:
                        print(f"[SIPStream] Error during fail-safe synthesis: {fe}")

                for w in tts_workers:
                    if not w.done():
                        w.cancel()
                await sentence_master_queue.put(None)

        async def consumer():
            nonlocal is_speaking, speaking_start_time
            is_final_call = False
            is_escalate_call = False
            escalate_target_number = ""
            chunk_idx = 0
            speaking_start_time = asyncio.get_event_loop().time()
            pacing_start = time.perf_counter()
            pacing_base = 0

            try:
                while True:
                    if current_gen != playback_generation:
                        break
                    s_queue = await sentence_master_queue.get()
                    if s_queue is None:
                        break
                    while True:
                        if current_gen != playback_generation:
                            break
                        item = await s_queue.get()
                        if item is None:
                            break
                        if isinstance(item, tuple) and item[0] == "end":
                            if item[1]:
                                is_final_call = True
                            if len(item) > 2 and item[2]:
                                is_escalate_call = True
                                escalate_target_number = item[3]
                            continue
                        if isinstance(item, bytes):
                            chunk_idx += 1
                            await send_media_msg(item)

                            # Jitter-buffered pacing: feed first 6 chunks (240ms) immediately to WebRTC buffer
                            if chunk_idx <= 6:
                                await asyncio.sleep(0.001)
                                pacing_start = time.perf_counter() + 0.12  # 120ms safety buffer ahead of real-time
                                pacing_base = chunk_idx
                            else:
                                target_t = pacing_start + ((chunk_idx - pacing_base) * 0.038)
                                sleep_dur = target_t - time.perf_counter()
                                if sleep_dur > 0.002:
                                    await asyncio.sleep(min(sleep_dur, 0.040))
                                else:
                                    await asyncio.sleep(0.001)
            except Exception as e:
                print(f"[SIPStream Consumer Error] {e}")
            finally:
                if current_gen == playback_generation:
                    # Hangover guard: Keep is_speaking=True for 400ms after the last chunk
                    # to ensure Asterisk WebRTC track completely plays out the last word without self-barge-in!
                    await asyncio.sleep(0.4)
                    if current_gen == playback_generation:
                        is_speaking = False
                if is_escalate_call and current_gen == playback_generation:
                    print(f"[SIPStream] 📞 Escalation transfer triggered for call {call_id} -> Target: {escalate_target_number}")
                    try:
                        await websocket.send_text(json.dumps({
                            "event": "transfer",
                            "target_number": escalate_target_number
                        }))
                    except Exception as e:
                        print(f"[SIPStream] Error signaling transfer to telephony client: {e}")
                if is_final_call and current_gen == playback_generation:
                    print(f"[SIPStream] 📴 Final farewell playback completed for call {call_id}. Signaling hangup to telephony client...")
                    try:
                        await websocket.send_text(json.dumps({"event": "hangup"}))
                    except Exception:
                        pass
                    # Allow 1.5s for audio queue in sip_client to completely play out to caller before closing websocket
                    await asyncio.sleep(1.5)
                    try:
                        await websocket.close(code=1000, reason="Completed normally")
                    except Exception:
                        pass

        producer_task = asyncio.create_task(producer())
        consumer_task = asyncio.create_task(consumer())
        playback_task = asyncio.gather(producer_task, consumer_task, return_exceptions=True)
        try:
            await playback_task
        except asyncio.CancelledError:
            pass

    # Main WebSocket Loop
    try:
        while True:
            message = await websocket.receive_text()
            if time.perf_counter() - sip_call_start > settings.MAX_CALL_DURATION_SECONDS:
                print(f"[SIPStream] ⏱️ Call {call_id} hit MAX_CALL_DURATION_SECONDS; hanging up.")
                await websocket.close(code=1000, reason="Maximum call duration reached")
                break
            try:
                data = json.loads(message)
            except (json.JSONDecodeError, TypeError) as jde:
                print(f"[SIPStream] Skipping malformed non-JSON frame: {jde}")
                continue
            event = data.get("event")

            # 1. START EVENT (Call Connected)
            if event == "start":
                caller_phone = data.get("caller") or caller_phone
                called_phone = data.get("called") or called_phone
                call_id = data.get("call_id") or call_id
                
                tid, aid = resolve_did_route(called_phone) if called_phone else ("", None)
                if not tid and caller_phone:
                    tid, aid = resolve_did_route(caller_phone)
                
                if not tid and call_id:
                    try:
                        with sync_engine.connect() as conn:
                            db_row = conn.execute(text("SELECT tenant_id, agent_id FROM calls WHERE call_id = :cid"), {"cid": call_id}).fetchone()
                            if db_row and db_row[0]:
                                tid = db_row[0]
                                aid = aid or db_row[1]
                    except Exception:
                        pass

                tenant_id = data.get("tenant_id") or tenant_id or tid or "cl_idigitalstudie_82d5"
                agent_id = data.get("agent_id") or agent_id or aid
                call_connect_time = asyncio.get_event_loop().time()

                print(f"[SIPStream] Call Started: call_id={call_id}, caller={caller_phone}, called={called_phone}, tenant={tenant_id}, agent={agent_id}")

                # Financial Guardrail: Verify prepaid wallet balance before processing call
                from app.services.tenant.tenant_service import check_tenant_wallet_balance
                bal_ok, curr_bal, bal_msg = check_tenant_wallet_balance(tenant_id, min_required=10.0)
                if not bal_ok:
                    print(f"[SIPStream] 🛑 Inbound SIP call rejected for {tenant_id}: {bal_msg}")
                    rejection_msg = "Kshama kijiye, is line par baat karne ke liye paryapt credit uplabdh nahi hai. Kripya apne administrator se sampark karein."
                    playback_task = asyncio.create_task(speak_text(rejection_msg, is_final=True))
                    continue

                agent = VoiceAgent(call_id=call_id, tenant_id=tenant_id, caller_number=caller_phone, agent_id=agent_id)
                # Pre-warm tenant knowledge in RAM so Turn 1 has 0ms lookup latency
                from app.core import rag_engine
                rag_engine.get_tenant_knowledge_text(tenant_id)
                greeting = agent.get_greeting()
                playback_task = asyncio.create_task(speak_text(greeting))

            # 2. TRANSFER FAILED EVENT (Asterisk trunk could not bridge to senior counselor)
            elif event == "transfer_failed":
                print(f"[SIPStream] ⚠️ Transfer failed on Asterisk gateway for call {call_id}. Initiating graceful apology and continuing call...")
                active_lang = agent.session.get("language", "hi-IN") if agent and hasattr(agent, "session") and agent.session else "hi-IN"
                if active_lang.startswith("en"):
                    apology = (
                        "I apologize, but our senior counselor is currently assisting another student on the line. "
                        "I have marked your query for an immediate priority callback on this number. "
                        "Meanwhile, how else may I help you with our courses or admissions?"
                    )
                else:
                    apology = (
                        "Maaf kijiye, hamare senior counselor abhi doosri call par vyast hain. "
                        "Maine aapka number priority callback ke liye note kar liya hai, hamari team aapse turant sampark karegi. "
                        "Tab tak kya main aapki kisi aur vishay mein sahayata kar sakti hoon?"
                    )

                # Reset escalation state so agent returns to active dialogue
                if agent and hasattr(agent, "session") and agent.session:
                    agent.session["state"] = "MAIN_LOOP"
                    agent.session["escalation_attempted"] = False
                    agent.session["transfer_requested"] = False

                # Auto-record priority callback lead in Leads CRM
                try:
                    from app.services.telemetry.call_logger import log_lead
                    log_lead(
                        tenant_id=tenant_id,
                        call_id=call_id,
                        name="Student Inquiry (Escalation Priority)",
                        phone=caller_phone,
                        reason="Senior Counselor Transfer Unavailable - Urgent Callback Requested"
                    )
                    print(f"[SIPStream] 📝 Auto-captured urgent callback lead for {caller_phone} in Leads CRM!")
                except Exception as le:
                    print(f"[SIPStream] Warning capturing callback lead: {le}")

                playback_task = asyncio.create_task(speak_text(apology))

            # 3. MEDIA EVENT (Caller 16-bit PCM Audio Chunk)
            elif event == "media":
                media_payload = data.get("media", {}).get("payload", "")
                if not media_payload:
                    continue

                raw_bytes = base64.b64decode(media_payload)
                if not raw_bytes:
                    continue

                energy = pcm_rms(raw_bytes, 2)

                # Barge-in check while agent is speaking
                # Provide immunity:
                # 1. 4.5s call connect window so pickup noise and line clicks never abort opening greeting
                # 2. 1.5s turn window from speaking_start_time so conversational fillers & opening sentence cannot be interrupted by self-echo or trailing speech
                call_elapsed = asyncio.get_event_loop().time() - call_connect_time
                turn_speech_elapsed = asyncio.get_event_loop().time() - speaking_start_time

                if is_speaking:
                    recent_audio_frames.append(raw_bytes)
                    can_barge_in = (call_elapsed > 1.2) and (turn_speech_elapsed > BARGE_IN_IMMUNITY_SECONDS)
                    if can_barge_in and energy >= BARGE_IN_ENERGY_THRESHOLD:
                        barge_in_frame_count += 1
                        if barge_in_frame_count >= BARGE_IN_SPEECH_TRIGGER_FRAMES:
                            print(f"[SIPStream] >>> BARGE-IN! Caller interrupted agent (energy={energy} >= {BARGE_IN_ENERGY_THRESHOLD}, sustained {barge_in_frame_count * 20}ms). Halting speech...")
                            await clear_playback()
                            is_caller_speaking = True
                            speech_frame_count = barge_in_frame_count
                            silence_frame_count = 0
                            barge_in_frame_count = 0
                            audio_buffer.clear()
                            for chunk in recent_audio_frames:
                                audio_buffer.extend(chunk)
                            recent_audio_frames.clear()
                    else:
                        # Smooth decay: allow natural syllable micro-pauses without instantly wiping out the counter
                        if barge_in_frame_count > 0:
                            barge_in_frame_count = max(0, barge_in_frame_count - 1)

                    if is_speaking:
                        continue

                # Caller turn VAD (only when AI is not speaking)
                barge_in_frame_count = 0
                recent_audio_frames.append(raw_bytes)

                if energy >= VAD_ENERGY_THRESHOLD:
                    speech_frame_count += 1
                    silence_frame_count = 0
                    if not is_caller_speaking and speech_frame_count >= VAD_SPEECH_TRIGGER_FRAMES:
                        is_caller_speaking = True
                        if not audio_buffer and recent_audio_frames:
                            for chunk in list(recent_audio_frames)[:-1]:
                                audio_buffer.extend(chunk)
                else:
                    silence_frame_count += 1
                    if silence_frame_count > 2:
                        speech_frame_count = 0

                if is_caller_speaking:
                    audio_buffer.extend(raw_bytes)

                # End of Turn detection
                # Adaptive Silence Threshold: allow 1.4s (70 frames) of breathing room when dictating email/phone
                sess_check = SESSIONS.get(call_id, {})
                b_ctx = sess_check.get("brochure_context", {})
                is_dictating = bool(b_ctx.get("active") and b_ctx.get("step") in ("ask_email", "ask_whatsapp_number"))
                effective_silence_frames = 70 if is_dictating else VAD_SILENCE_TRIGGER_FRAMES

                if is_caller_speaking and silence_frame_count >= effective_silence_frames:
                    is_caller_speaking = False
                    speech_frame_count = 0
                    silence_frame_count = 0

                    if len(audio_buffer) >= MIN_SPEECH_BYTES:
                        captured_audio = bytes(audio_buffer)
                        audio_dur = round(len(captured_audio) / 16000.0, 2)
                        audio_buffer.clear()
                        lang_hint = agent.session.get("language", "en-IN") if agent and hasattr(agent, "session") and agent.session else "en-IN"
                        user_text, detected_lang = await transcribe_audio_pcm(
                            captured_audio, 
                            sample_rate=8000, 
                            language_hint=lang_hint,
                            is_dictating=is_dictating
                        )
                        if call_id in SESSIONS:
                            SESSIONS[call_id]["stt_audio_seconds"] = SESSIONS[call_id].get("stt_audio_seconds", 0.0) + audio_dur
                            if user_text:
                                SESSIONS[call_id]["stt_characters"] = SESSIONS[call_id].get("stt_characters", 0) + len(user_text)
                        if user_text and len(user_text.strip()) > 1:
                            consecutive_inaudible_count = 0
                            if agent and hasattr(agent, "session") and agent.session:
                                from app.core.voice_agent import detect_language_switch_intent
                                switch_intent = detect_language_switch_intent(user_text, tenant_id=agent.tenant_id, config=agent.config)
                                if switch_intent:
                                    agent.session["language"] = switch_intent[0]
                                elif any(k in user_text.lower() for k in ["speak in english", "speak english", "in english", "english please", "switch to english", "talk in english", "can you speak english", "only english"]):
                                    agent.session["language"] = "en-IN"
                                elif detected_lang:
                                    neutral_words = {"hello", "hi", "hey", "ok", "okay", "yes", "yeah", "yep", "hmm"}
                                    if user_text.lower().strip() in neutral_words and agent.session.get("language"):
                                        pass
                                    else:
                                        agent.session["language"] = detected_lang
                                elif not agent.session.get("language"):
                                    agent.session["language"] = detected_lang or "en-IN"
                                current_lang = agent.session.get("language")
                                print(f"[SIPStream] 🎙️ Caller Audio: '{user_text}' | Detected Lang: {detected_lang} -> Active Session Lang: {current_lang}")
                            await clear_playback()
                            turn_task = asyncio.create_task(process_caller_turn(user_text))
                        else:
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
                                playback_task = asyncio.create_task(speak_text(transfer_msg, is_escalation=True, esc_num=esc_num))
                    else:
                        audio_buffer.clear()

            # 3. STOP EVENT (Call Ended)
            elif event == "stop":
                print(f"[SIPStream] Received stop event for call_id={call_id}")
                break

    except (WebSocketDisconnect, RuntimeError):
        print(f"[SIPStream] WebSocket closed for call_id={call_id}")
    except Exception as e:
        print(f"[SIPStream] WebSocket error: {e}")
        traceback.print_exc()
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
            is_tr = sess.get("transfer_requested", False) or sess.get("state") == "TRANSFERRED"
            outcome = "transferred" if is_tr else ("completed" if sess.get("state") == "COMPLETED" else "ended")
            try:
                _log_call_end(call_id, outcome, is_tr)
            except Exception as le:
                print(f"[SIPStream] Error logging call end: {le}")
            try:
                generate_executive_summary(call_id, outcome=outcome)
            except Exception as e:
                print(f"[SIPStream] Summary error: {e}")
        print(f"[SIPStream] Call session closed: {call_id}")


@router.post("/sip/reconnect", tags=["Asterisk WebRTC SIP Stream"])
async def reconnect_sip(admin_user: dict = Depends(get_current_admin)):
    """Forces Asterisk WebRTC SIP client to disconnect and reconnect with a fresh REGISTER."""
    try:
        from app.core.sip_client import get_asterisk_sip_client
        sc = get_asterisk_sip_client()
        if sc.ws:
            await sc.ws.close()
            return {"status": "reconnecting", "message": f"Closed existing WebSocket for {sc.username}, client loop will reconnect immediately."}
        else:
            asyncio.create_task(sc.start())
            return {"status": "started", "message": f"Started SIP client loop for {sc.username}."}
    except Exception as e:
        print(f"[SIPStream] Reconnect error: {e}")
        return {"status": "error", "error": "Failed to reconnect SIP client."}



@router.get("/sip/status", tags=["Asterisk WebRTC SIP Stream"])
async def sip_status(admin_user: dict = Depends(get_current_admin)):
    """Returns current Asterisk SIP client status."""
    from app.core.sip_client import get_asterisk_sip_client
    sc = get_asterisk_sip_client()
    return {
        "user": sc.username,
        "host": sc.host,
        "did_number": sc.did_number,
        "is_running": sc.is_running,
        "is_registered": sc.is_registered,
        "active_calls_count": len(sc.active_calls)
    }

