"""
Asterisk WebRTC SIP Client (aiortc + SIP over WebSocket)
Connects to Asterisk (kenyavoice.rpdigitalphone.com:5000/ws),
registers the SIP extension with Digest Authentication,
answers incoming calls, and bridges WebRTC audio with QwickCall's AI VoiceAgent pipeline.
"""
import os
import re
import ssl
import json
import uuid
import time
import fractions
import base64
import asyncio
import traceback
from typing import Optional, Dict, Any, Set, List
import websockets


from aiortc import RTCPeerConnection, RTCSessionDescription, AudioStreamTrack, RTCRtpSender
from aiortc.mediastreams import MediaStreamError
import av

from app.config import settings
from app.core.sip_parser import (
    SipMessage,
    create_register_request,
    parse_auth_challenge,
    build_auth_header,
    create_200_ok_response,
    create_options_200_ok,
    create_trying_response,
    create_ringing_response,
    create_bye_request,
    create_refer_request,
    create_invite_request,
    create_ack_request
)

import logging
from logging.handlers import RotatingFileHandler

LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "sip_debug.log")

# Pipe aiortc and aioice logs to bounded rotating sip_debug.log
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8")
file_handler.setLevel(logging.INFO)
formatter = logging.Formatter("[%(asctime)s] [%(name)s] %(message)s")
file_handler.setFormatter(formatter)

for logger_name in ["aiortc", "aioice", "aiortc.rtcdtlstransport"]:
    l = logging.getLogger(logger_name)
    l.setLevel(logging.INFO)
    l.addHandler(file_handler)

# Runtime patch for aiortc DTLS cipher list to support Asterisk RSA certificates
try:
    from aiortc.rtcdtlstransport import RTCCertificate
    _orig_ssl_ctx = RTCCertificate._create_ssl_context

    def _patched_ssl_ctx(self, srtp_profiles):
        ctx = _orig_ssl_ctx(self, srtp_profiles)
        ctx.set_cipher_list(
            b"ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-ECDSA-AES128-SHA:ECDHE-ECDSA-AES256-SHA:"
            b"ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-CHACHA20-POLY1305:ECDHE-RSA-AES128-SHA:ECDHE-RSA-AES256-SHA:"
            b"AES128-GCM-SHA256:AES128-SHA:AES256-SHA:HIGH:!aNULL:!eNULL:!MD5:!3DES"
        )
        return ctx

    RTCCertificate._create_ssl_context = _patched_ssl_ctx
except Exception as e:
    print(f"Warning: could not patch RTCCertificate._create_ssl_context: {e}")

def _log_sip(text: str):
    import datetime
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    line = f"[{now}] {text}\n"
    print(text)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


class OutboundAudioTrack(AudioStreamTrack):
    """WebRTC Audio track that sends AI synthesized speech frames to the caller."""
    kind = "audio"

    def __init__(self):
        super().__init__()
        self.queue: asyncio.Queue = asyncio.Queue()
        self._sample_rate = 8000
        self._start: Optional[float] = None
        self._timestamp = 0

    def put_pcm(self, pcm_bytes: bytes):
        """Pushes raw 16-bit Linear PCM audio bytes into the track queue in 20ms (320-byte) frames."""
        frame_size = 320  # 160 samples * 2 bytes for 8kHz mono
        for i in range(0, len(pcm_bytes), frame_size):
            chunk = pcm_bytes[i:i + frame_size]
            if len(chunk) < frame_size:
                chunk += b"\x00" * (frame_size - len(chunk))
            self.queue.put_nowait(chunk)

    def clear(self):
        """Clears pending playback queue for instant barge-in."""
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except Exception:
                break

    def reset_timing(self):
        """Smoothly aligns the RTP clock baseline without resetting the monotonic timestamp."""
        self.clear()
        if self._start is not None and self._timestamp > 0:
            # Re-anchor _start to the current wall-clock time maintaining the exact current _timestamp
            self._start = time.time() - (self._timestamp / self._sample_rate)

    async def recv(self):
        """Generates next 20ms audio frame for WebRTC transmission with precise real-time pacing."""
        if self.readyState != "live":
            raise MediaStreamError

        samples = 160  # 20ms at 8000Hz

        # Wall-clock real-time pacing (exact 20ms per frame)
        if self._start is None:
            self._start = time.time()
            self._timestamp = 0

        self._timestamp += samples
        wait = self._start + (self._timestamp / self._sample_rate) - time.time()

        # If the event loop fell behind by more than 150ms, smoothly resynchronize _start
        # without forcing artificial extra delay
        if wait < -0.15:
            self._start = time.time() - (self._timestamp / self._sample_rate)
            wait = 0.0

        if wait > 0:
            await asyncio.sleep(wait)

        # Fetch audio from queue or use comfort silence
        try:
            pcm_bytes = self.queue.get_nowait()
        except asyncio.QueueEmpty:
            pcm_bytes = b"\x00" * 320

        frame = av.AudioFrame(format="s16", layout="mono", samples=samples)
        frame.planes[0].update(pcm_bytes)
        frame.sample_rate = self._sample_rate
        frame.pts = self._timestamp
        frame.time_base = fractions.Fraction(1, self._sample_rate)
        return frame


class AsteriskSipClient:
    def __init__(
        self,
        host: str = "kenyavoice.rpdigitalphone.com",
        port: int = 5000,
        path: str = "/ws",
        username: str = "",
        password: str = "",
        did_number: str = ""
    ):
        self.host = host
        self.port = port
        self.path = path
        self.username = username or settings.RPDIGITAL_SIP_USER
        self.password = password or settings.RPDIGITAL_SIP_PASSWORD
        self.did_number = did_number or settings.RPDIGITAL_DID_NUMBER

        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.is_registered = False
        self.is_running = False
        self._reg_call_id: Optional[str] = None
        self._reg_from_tag: Optional[str] = None
        self._ws_instance: str = uuid.uuid4().hex[:12]
        self._cseq = 1

        # Active call peer connections: call_id -> RTCPeerConnection
        self.active_calls: Dict[str, RTCPeerConnection] = {}
        # Active call SIP dialog details: call_id -> dict
        self.active_dialogs: Dict[str, Dict[str, Any]] = {}
        # Transferred calls that must NOT be terminated with SIP BYE by our client: set of call_ids
        self.transferred_calls: Set[str] = set()
        # Currently terminating calls to prevent duplicate re-entrant SIP BYE race conditions
        self._terminating_calls: Set[str] = set()
        # Pending outbound calls: call_id -> dict
        self.outbound_calls: Dict[str, Dict[str, Any]] = {}
        # Active FastAPI /sip/stream WebSocket bridges: call_id -> WebSocket
        self._fastapi_bridges: Dict[str, Any] = {}
        self._auth_attempts = 0

    def _reload_env_config(self):
        """Dynamically re-reads Asterisk credentials from environment or .env without crashing in containers."""
        self.username = os.getenv("RPDIGITAL_SIP_USER") or getattr(settings, "RPDIGITAL_SIP_USER", "") or self.username
        self.password = os.getenv("RPDIGITAL_SIP_PASSWORD") or getattr(settings, "RPDIGITAL_SIP_PASSWORD", "") or self.password
        self.did_number = os.getenv("RPDIGITAL_DID_NUMBER") or getattr(settings, "RPDIGITAL_DID_NUMBER", "") or self.did_number

        # Optional dev convenience fallback to .env if file is present
        for p in [".env", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env")]:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith("RPDIGITAL_SIP_USER=") and not self.username:
                                self.username = line.split("=", 1)[1].strip().strip('"').strip("'")
                            elif line.startswith("RPDIGITAL_SIP_PASSWORD=") and not self.password:
                                self.password = line.split("=", 1)[1].strip().strip('"').strip("'")
                            elif line.startswith("RPDIGITAL_DID_NUMBER=") and not self.did_number:
                                self.did_number = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
                except Exception:
                    pass

    async def start(self):
        """Starts the persistent Asterisk SIP WebRTC client background worker."""
        self._reload_env_config()
        if not self.username or not self.password:
            print("[AsteriskSIP] ⚠️ SIP credentials missing (RPDIGITAL_SIP_USER, RPDIGITAL_SIP_PASSWORD). Skipping connection.")
            return

        self.is_running = True
        print(f"[AsteriskSIP] Starting Asterisk SIP WebRTC Client for extension: {self.username}...")
        while self.is_running:
            try:
                self._reload_env_config()
                self._auth_attempts = 0
                await self._run_client_loop()
            except Exception as e:
                print(f"[AsteriskSIP] Client loop exception: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)

    async def stop(self):
        """Cleanly unregisters extension from Asterisk and closes WebSocket connection."""
        self.is_running = False
        if self.ws and self.is_registered:
            try:
                self._cseq += 1
                req, _, _ = create_register_request(
                    self.username,
                    self.host,
                    self.port,
                    call_id=self._reg_call_id,
                    cseq=self._cseq,
                    from_tag=self._reg_from_tag,
                    expires=0,
                    ws_instance=self._ws_instance
                )
                await self.ws.send(req)
                print(f"[AsteriskSIP] Sent clean un-REGISTER (expires=0) for {self.username}")
            except Exception:
                pass
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass


    async def _keepalive_reregister(self):
        """Sends periodic SIP REGISTER to refresh Asterisk registration before 300s expiry."""
        while self.is_running and self.ws:
            try:
                await asyncio.sleep(120)
                if self.is_registered and self.ws:
                    self._cseq += 1
                    req, _, _ = create_register_request(
                        self.username,
                        self.host,
                        self.port,
                        call_id=self._reg_call_id,
                        cseq=self._cseq,
                        from_tag=self._reg_from_tag,
                        ws_instance=self._ws_instance
                    )
                    await self.ws.send(req)
                    print(f"[AsteriskSIP] Sent keepalive re-REGISTER for {self.username} (cseq={self._cseq})")
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[AsteriskSIP] Re-registration loop error: {e}")
                break

    async def _run_client_loop(self):
        uri = f"wss://{self.host}:{self.port}{self.path}"
        ssl_ctx = ssl.create_default_context()
        if not settings.RPDIGITAL_SIP_TLS_VERIFY:
            # Lab/self-signed PBX only: disabling verification exposes SIP signalling to MITM
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE

        async with websockets.connect(uri, ssl=ssl_ctx, subprotocols=["sip"], ping_interval=20, ping_timeout=20) as ws:
            self.ws = ws
            print(f"[AsteriskSIP] Connected to Asterisk WebSocket: {uri}")

            # Send initial REGISTER
            req, self._reg_call_id, self._reg_from_tag = create_register_request(
                self.username, self.host, self.port, cseq=self._cseq, ws_instance=self._ws_instance
            )
            await ws.send(req)
            print(f"[AsteriskSIP] Sent initial REGISTER for {self.username}")

            rereg_task = asyncio.create_task(self._keepalive_reregister())
            try:
                async for msg_text in ws:
                    first_line = msg_text.splitlines()[0] if msg_text else ""
                    _log_sip(f"SIP In: {first_line}")
                    try:
                        await self._handle_sip_message(msg_text)
                    except Exception as e:
                        _log_sip(f"❌ [AsteriskSIP] Error processing SIP message: {e}")
                        import traceback
                        _log_sip(traceback.format_exc())
            finally:
                rereg_task.cancel()
                self.is_registered = False
                for pc in list(self.calls.values()):
                    try:
                        asyncio.create_task(pc.close())
                    except Exception:
                        pass
                self.calls.clear()
                self._fastapi_bridges.clear()

    async def _handle_sip_message(self, raw_text: str):
        msg = SipMessage(raw_text)

        # 1. SIP RESPONSES
        if msg.is_response:
            cseq_str = msg.get_header("cseq", "")
            call_id = msg.get_header("call-id", "")

            # A. Responses for outbound calls initiated by this client
            if call_id in self.outbound_calls:
                ob = self.outbound_calls[call_id]
                pc: RTCPeerConnection = ob["pc"]

                # 401 / 407 Digest Challenge for INVITE
                if msg.status_code in (401, 407):
                    auth_val = msg.get_header("proxy-authenticate") or msg.get_header("www-authenticate")
                    challenge = parse_auth_challenge(auth_val)
                    realm = challenge.get("realm", "asterisk")
                    nonce = challenge.get("nonce", "")
                    opaque = challenge.get("opaque", "")
                    qop = challenge.get("qop", "auth")
                    uri_str = f"sip:{ob['dial_target']}@{self.host}"
                    auth_header = build_auth_header(
                        self.username,
                        self.password,
                        realm,
                        nonce,
                        uri_str,
                        method="INVITE",
                        qop=qop,
                        opaque=opaque
                    )
                    ob["cseq"] += 1
                    inv_auth, _, _ = create_invite_request(
                        from_user=self.username,
                        to_number=ob["dial_target"],
                        domain=self.host,
                        port=self.port,
                        sdp_offer=ob["sdp_offer"],
                        call_id=call_id,
                        cseq=ob["cseq"],
                        from_tag=ob["from_tag"],
                        auth_header=auth_header
                    )
                    _log_sip(f"[AsteriskSIP] Sent authenticated INVITE for outbound call {call_id} to {ob['dial_target']}")
                    await self.ws.send(inv_auth)
                    return

                # 100 Trying
                elif msg.status_code == 100:
                    _log_sip(f"[AsteriskSIP] ⏳ Asterisk 100 Trying for outbound call {call_id}")
                    return

                # 180 Ringing / 183 Session Progress (Early media)
                elif msg.status_code in (180, 183):
                    _log_sip(f"[AsteriskSIP] 📞 Destination phone is ringing for call {call_id} ({msg.status_code} {msg.reason_phrase})")
                    early_evt = ob.get("early_media_event")
                    if early_evt and not early_evt.is_set():
                        early_evt.set()
                    if msg.body and not pc.remoteDescription:
                        try:
                            await pc.setRemoteDescription(RTCSessionDescription(sdp=msg.body, type="answer"))
                        except Exception as e:
                            _log_sip(f"[AsteriskSIP] Warning setting early media SDP for {call_id}: {e}")
                    return

                # 200 OK (Call Answered!)
                elif msg.status_code == 200 and "INVITE" in cseq_str:
                    _log_sip(f"[AsteriskSIP] ✅ Customer answered outbound call {call_id}!")
                    early_evt = ob.get("early_media_event")
                    if early_evt and not early_evt.is_set():
                        early_evt.set()
                    ans_evt = ob.get("answered_event")
                    if ans_evt and not ans_evt.is_set():
                        ans_evt.set()


                    if msg.body:
                        try:
                            if not pc.remoteDescription or pc.remoteDescription.type != "answer":
                                await pc.setRemoteDescription(RTCSessionDescription(sdp=msg.body, type="answer"))
                        except Exception as e:
                            _log_sip(f"[AsteriskSIP] Warning setting 200 OK answer SDP for {call_id}: {e}")

                    to_hdr = msg.get_header("to")
                    from_hdr = f"<sip:{self.username}@{self.host}>;tag={ob['from_tag']}"
                    contact = msg.get_header("contact") or f"sip:{ob['dial_target']}@{self.host}"
                    route = msg.get_header("record-route")
                    cseq_num = int(cseq_str.split()[0]) if cseq_str.split()[0].isdigit() else 2
                    ack = create_ack_request(
                        from_uri=from_hdr,
                        to_uri=to_hdr,
                        request_uri=contact,
                        call_id=call_id,
                        cseq_num=cseq_num,
                        route=route
                    )
                    await self.ws.send(ack)
                    _log_sip(f"[AsteriskSIP] Sent ACK for outbound call {call_id}")

                    # Store active dialog for BYE
                    self.active_dialogs[call_id] = {
                        "from_header": from_hdr,
                        "to_header": to_hdr,
                        "to_tag": to_hdr.split(";tag=")[1].split(";")[0] if ";tag=" in to_hdr else "",
                        "contact": contact,
                        "route": route
                    }
                    return

                # 4xx / 5xx / 6xx (Call Failed / Rejected / Busy)
                elif msg.status_code >= 400:
                    _log_sip(f"[AsteriskSIP] ❌ Outbound call {call_id} failed with status {msg.status_code}: {msg.reason_phrase}")
                    ob["failure_error"] = f"{msg.status_code} {msg.reason_phrase}"
                    early_evt = ob.get("early_media_event")
                    if early_evt and not early_evt.is_set():
                        early_evt.set()
                    ans_evt = ob.get("answered_event")
                    if ans_evt and not ans_evt.is_set():
                        ans_evt.set()
                    try:
                        await pc.close()
                    except Exception:
                        pass
                    self.outbound_calls.pop(call_id, None)
                    return

            # B. Transfer REFER responses
            if "REFER" in cseq_str:
                _log_sip(f"[AsteriskSIP] 📞 Asterisk REFER response for {call_id}: {msg.status_code} {msg.reason_phrase}")
                if msg.status_code == 202:
                    _log_sip(f"[AsteriskSIP] ✅ Asterisk accepted call transfer (202 Accepted) for {call_id}!")
                elif msg.status_code >= 400:
                    _log_sip(f"❌ [AsteriskSIP] Asterisk rejected call transfer ({msg.status_code} {msg.reason_phrase}) for {call_id}!")
                    self.transferred_calls.discard(call_id)
                    if call_id in self._fastapi_bridges:
                        ws_bridge = self._fastapi_bridges[call_id]
                        try:
                            await ws_bridge.send(json.dumps({
                                "event": "transfer_failed",
                                "call_id": call_id,
                                "error": f"Asterisk rejected REFER: {msg.status_code} {msg.reason_phrase}"
                            }))
                        except Exception:
                            pass

            # C. BYE responses
            elif "BYE" in cseq_str:
                _log_sip(f"[AsteriskSIP] ✅ Asterisk confirmed SIP BYE (Status: {msg.status_code} {msg.reason_phrase}) for call {call_id} — carrier trunk line released!")

            # D. Registration responses
            if "REGISTER" in cseq_str:
                # 401 Unauthorized -> Compute Digest Response
                if msg.status_code == 401:
                    self._auth_attempts = getattr(self, "_auth_attempts", 0) + 1
                    if self._auth_attempts > 2:
                        print(f"[AsteriskSIP] ❌ Authentication failed for user '{self.username}'. Please verify RPDIGITAL_SIP_USER and RPDIGITAL_SIP_PASSWORD in .env.")
                        return

                    auth_val = msg.get_header("www-authenticate")
                    challenge = parse_auth_challenge(auth_val)
                    realm = challenge.get("realm", "asterisk")
                    nonce = challenge.get("nonce", "")
                    opaque = challenge.get("opaque", "")
                    qop = challenge.get("qop", "auth")

                    self._cseq += 1
                    uri = f"sip:{self.host}:{self.port}"
                    auth_header = build_auth_header(
                        self.username,
                        self.password,
                        realm,
                        nonce,
                        uri,
                        method="REGISTER",
                        qop=qop,
                        opaque=opaque
                    )
                    req, _, _ = create_register_request(
                        self.username,
                        self.host,
                        self.port,
                        call_id=self._reg_call_id,
                        cseq=self._cseq,
                        from_tag=self._reg_from_tag,
                        auth_header=auth_header,
                        ws_instance=self._ws_instance
                    )
                    await self.ws.send(req)
                    print(f"[AsteriskSIP] Sent authenticated REGISTER with Digest challenge (attempt {self._auth_attempts})")

                # 200 OK -> Registered Successfully
                elif msg.status_code == 200:
                    self.is_registered = True
                    self._auth_attempts = 0
                    contacts = msg.get_header("contact")
                    _log_sip(f"✅ [AsteriskSIP] Extension {self.username} REGISTERED successfully (cseq={cseq_str})! Active Contacts in Asterisk: {contacts}")

        # 2. SIP REQUESTS (Incoming Asterisk Events)
        elif msg.is_request:
            if msg.method == "OPTIONS":
                resp = create_options_200_ok(msg, self.username, self.host)
                await self.ws.send(resp)
                _log_sip(f"[AsteriskSIP] Responded 200 OK to Asterisk qualify OPTIONS ping.")

            elif msg.method == "INVITE":
                call_id = msg.get_header("call-id")
                from_hdr = msg.get_header("from")
                to_tag = uuid.uuid4().hex[:8]
                _log_sip(f"\n📞 [AsteriskSIP] Incoming INVITE call: Call-ID={call_id}, From={from_hdr}, to_tag={to_tag}")
                # Immediately send 100 Trying so carrier doesn't lock into a 3-4s ring cycle before 200 OK cut-through
                trying = create_trying_response(msg)
                await self.ws.send(trying)
                _log_sip(f"[AsteriskSIP] Sent 100 Trying for Call-ID={call_id}")
                asyncio.create_task(self._handle_incoming_call(msg, to_tag=to_tag))

            elif msg.method == "ACK":
                call_id = msg.get_header("call-id")
                _log_sip(f"[AsteriskSIP] Received ACK from Asterisk for Call-ID={call_id}")

            elif msg.method == "CANCEL":
                call_id = msg.get_header("call-id")
                _log_sip(f"[AsteriskSIP] Received CANCEL for Call-ID={call_id}")
                resp = create_options_200_ok(msg, self.username, self.host)
                await self.ws.send(resp)
                self.active_dialogs.pop(call_id, None)
                pc = self.active_calls.pop(call_id, None)
                if pc:
                    await pc.close()

            elif msg.method == "BYE":
                call_id = msg.get_header("call-id")
                _log_sip(f"[AsteriskSIP] Received BYE for Call-ID={call_id}")
                _log_sip(f"[AsteriskSIP] BYE details:\n{msg.raw_text.strip()}")
                resp = create_options_200_ok(msg, self.username, self.host)
                await self.ws.send(resp)
                self.active_dialogs.pop(call_id, None)
                self.transferred_calls.discard(call_id)
                pc = self.active_calls.pop(call_id, None)
                if pc:
                    await pc.close()

            elif msg.method == "NOTIFY":
                call_id = msg.get_header("call-id")
                _log_sip(f"[AsteriskSIP] Received NOTIFY for Call-ID={call_id}:\n{msg.raw_text.strip()}")
                resp = create_options_200_ok(msg, self.username, self.host)
                await self.ws.send(resp)

                event_hdr = (msg.get_header("event") or "").lower()
                sub_state = (msg.get_header("subscription-state") or "").lower()
                body = (msg.body or "").strip()

                if "refer" in event_hdr:
                    if "200 OK" in body or "180 Ringing" in body:
                        _log_sip(f"[AsteriskSIP] 🎉 Asterisk transfer in progress/succeeded ({body.splitlines()[0] if body else '200 OK'}) for call {call_id}!")
                    elif "terminated" in sub_state or any(code in body for code in ["503", "486", "404", "500", "487", "603"]):
                        if "100 Trying" not in body and "200 OK" not in body:
                            _log_sip(f"⚠️ [AsteriskSIP] Asterisk reported transfer failure for {call_id}: {body or sub_state}")
                            dialog = self.active_dialogs.get(call_id)
                            tf = dialog.get("transfer_info") if dialog else None

                            # Attempt 2: retry with 10 digits without '0' prefix
                            if tf and tf.get("attempt") == 1 and tf.get("phone_10") != tf.get("dial_with_0"):
                                tf["attempt"] = 2
                                tf["cseq"] += 1
                                alt_uri = f"<sip:{tf['phone_10']}@{tf['pbx_host']}>"
                                _log_sip(f"[AsteriskSIP] 🔄 Retrying SIP REFER with alternate 10-digit format: {alt_uri}...")
                                await self._send_refer(call_id, alt_uri, tf["cseq"])
                                return
                            # Attempt 3: retry with 91 prefix
                            elif tf and tf.get("attempt") == 2:
                                tf["attempt"] = 3
                                tf["cseq"] += 1
                                alt_uri = f"<sip:91{tf['phone_10']}@{tf['pbx_host']}>"
                                _log_sip(f"[AsteriskSIP] 🔄 Retrying SIP REFER with 91-prefix format: {alt_uri}...")
                                await self._send_refer(call_id, alt_uri, tf["cseq"])
                                return

                            # All REFER attempts failed on Asterisk trunk!
                            _log_sip(f"❌ [AsteriskSIP] All transfer attempts failed for call {call_id}. Discarding transfer state so call stays connected!")
                            self.transferred_calls.discard(call_id)
                            if call_id in self._fastapi_bridges:
                                ws_bridge = self._fastapi_bridges[call_id]
                                try:
                                    await ws_bridge.send(json.dumps({
                                        "event": "transfer_failed",
                                        "call_id": call_id,
                                        "target_number": tf.get("target_number") if tf else "",
                                        "error": body or "Asterisk 503 Service Unavailable"
                                    }))
                                except Exception as we:
                                    _log_sip(f"Error signaling transfer_failed to FastAPI: {we}")

    async def hangup_call(self, call_id: str):
        """Tears down WebRTC and sends standard RFC 3261 SIP BYE to Asterisk to immediately hang up the caller's mobile."""
        was_transferred = call_id in self.transferred_calls
        if was_transferred:
            self.transferred_calls.discard(call_id)

        # Prevent duplicate concurrent/re-entrant hangup calls
        if hasattr(self, "_terminating_calls") and call_id in self._terminating_calls:
            _log_sip(f"[AsteriskSIP] 📴 Hangup already in progress for call {call_id}. Skipping duplicate.")
            return

        if not hasattr(self, "_terminating_calls"):
            self._terminating_calls = set()
        self._terminating_calls.add(call_id)

        dialog = self.active_dialogs.pop(call_id, None)
        pc = self.active_calls.pop(call_id, None)

        if was_transferred:
            _log_sip(f"[AsteriskSIP] 📞 Call {call_id} was transferred via REFER. Closing local WebRTC, but SKIPPING SIP BYE so Asterisk keeps caller connected to target phone!")
            if pc:
                try:
                    await pc.close()
                except Exception:
                    pass
            self._terminating_calls.discard(call_id)
            return

        # 1. SEND SIP BYE FIRST (BEFORE CLOSING WEBRTC)
        # We shield this async operation so that parent task cancellation CANNOT abort the SIP BYE request!
        async def _send_bye():
            if not self.ws:
                _log_sip(f"❌ [AsteriskSIP] Cannot send SIP BYE for call {call_id}: SIP WebSocket is not connected!")
                return

            try:
                d = dialog or {}
                is_outbound = d.get("is_outbound", False) or call_id.startswith("sip_out_")
                if is_outbound:
                    from_hdr = d.get("from_header", f"<sip:{self.username}@{self.host}>")
                    to_hdr = d.get("to_header", "")
                else:
                    from_hdr = d.get("to_header", f"<sip:{self.username}@{self.host}>")
                    if ";tag=" not in from_hdr and d.get("to_tag"):
                        from_hdr = f"{from_hdr};tag={d['to_tag']}"
                    to_hdr = d.get("from_header", "")

                contact = d.get("contact") or f"<sip:{self.host}:5000;transport=ws>"
                route = d.get("route")

                bye_msg = create_bye_request(
                    call_id=call_id,
                    from_uri=from_hdr,
                    to_uri=to_hdr,
                    request_uri=contact,
                    cseq=100,
                    route=route
                )
                await self.ws.send(bye_msg)
                _log_sip(f"[AsteriskSIP] 📴 Sent SIP BYE to Asterisk for call {call_id} — mobile call hung up successfully!\n{bye_msg.strip()}")
            except Exception as e:
                _log_sip(f"❌ [AsteriskSIP] Error sending BYE for call {call_id}: {e}")
                import traceback
                _log_sip(traceback.format_exc())

        try:
            await asyncio.shield(_send_bye())
        except Exception as e:
            _log_sip(f"❌ [AsteriskSIP] Shield error sending BYE for {call_id}: {e}")

        # 2. TEAR DOWN WEBRTC (AFTER SIP BYE HAS BEEN TRANSMITTED)
        if pc:
            try:
                await pc.close()
            except Exception:
                pass

        self._terminating_calls.discard(call_id)

    async def _send_refer(self, call_id: str, refer_to_uri: str, cseq: int = 101) -> bool:
        """Internal helper to dispatch a SIP REFER message."""
        dialog = self.active_dialogs.get(call_id)
        if not dialog or not self.ws:
            return False
        try:
            remote_contact = dialog.get("contact") or f"<sip:{self.host}:5000;transport=ws>"
            from_hdr = dialog.get("to_header", f"<sip:{self.username}@{self.host}>")
            if ";tag=" not in from_hdr and dialog.get("to_tag"):
                from_hdr = f"{from_hdr};tag={dialog['to_tag']}"
            to_hdr = dialog.get("from_header", "")
            route = dialog.get("route")
            local_contact = f"<sip:{self.username}@{self.host};transport=ws>"

            refer_msg = create_refer_request(
                call_id=call_id,
                from_uri=from_hdr,
                to_uri=to_hdr,
                refer_to_uri=refer_to_uri,
                contact_uri=local_contact,
                request_uri=remote_contact,
                cseq=cseq,
                route=route
            )
            await self.ws.send(refer_msg)
            self.transferred_calls.add(call_id)
            _log_sip(f"[AsteriskSIP] 📞 Sent SIP REFER (cseq={cseq}) for call {call_id} -> Refer-To: {refer_to_uri}")
            return True
        except Exception as e:
            _log_sip(f"❌ [AsteriskSIP] Error sending REFER for call {call_id}: {e}")
            return False

    async def transfer_call(self, call_id: str, target_number: str) -> bool:
        """
        Sends SIP REFER (RFC 3515) to Asterisk PBX to transfer the active call to a human counselor phone number.
        Formats the target number specifically for Indian Asterisk PBX PSTN trunks (e.g. 07022721098@indore147server).
        """
        dialog = self.active_dialogs.get(call_id)
        if not dialog or not self.ws:
            _log_sip(f"❌ [AsteriskSIP] Cannot transfer call {call_id}: No active dialog found.")
            return False

        try:
            clean_num = target_number.strip().replace(" ", "").replace("-", "")
            digits = re.sub(r"\D", "", clean_num)

            # Extract 10-digit Indian phone number
            if digits.startswith("91") and len(digits) == 12:
                phone_10 = digits[2:]
            elif digits.startswith("0") and len(digits) == 11:
                phone_10 = digits[1:]
            elif len(digits) == 10:
                phone_10 = digits
            else:
                phone_10 = digits

            # Indian Asterisk PBX PSTN trunks match 0 + 10 digits (e.g. 07022721098)
            dial_with_0 = f"0{phone_10}" if len(phone_10) == 10 else phone_10

            to_hdr = dialog.get("from_header", "")
            remote_contact = dialog.get("contact") or ""

            # Extract Asterisk PBX domain from incoming dialog (e.g. indore147server)
            pbx_host = None
            if remote_contact and "@" in remote_contact:
                pbx_host = remote_contact.split("@")[1].split(">")[0].split(";")[0].split(":")[0].strip()
            if not pbx_host and to_hdr and "@" in to_hdr:
                pbx_host = to_hdr.split("@")[1].split(">")[0].split(";")[0].split(":")[0].strip()
            if not pbx_host:
                pbx_host = self.host

            # Store transfer metadata for retry & fail-safe handling in NOTIFY
            dialog["transfer_info"] = {
                "target_number": target_number,
                "phone_10": phone_10,
                "dial_with_0": dial_with_0,
                "pbx_host": pbx_host,
                "attempt": 1,
                "cseq": 101
            }

            # Primary Attempt: Dial with 0 prefix using Asterisk PBX domain
            refer_to_uri = f"<sip:{dial_with_0}@{pbx_host}>"
            return await self._send_refer(call_id, refer_to_uri, cseq=101)
        except Exception as e:
            _log_sip(f"❌ [AsteriskSIP] Error preparing REFER for call {call_id}: {e}")
            return False

    async def _handle_incoming_call(self, invite_msg: SipMessage, to_tag: Optional[str] = None):
        """Answers incoming call, sets up WebRTC audio tracks, and bridges with /sip/stream."""
        call_id = invite_msg.get_header("call-id")
        try:
            from_hdr = invite_msg.get_header("from")
            to_hdr = invite_msg.get_header("to")

            caller = from_hdr.split("<")[1].split(">")[0].replace("sip:", "").split("@")[0] if "<" in from_hdr else "unknown"
            
            # Check for Asterisk caller ID packing (e.g. CallerNumber0091DIDNumber)
            extracted_did = ""
            if "0091" in caller and len(caller) >= 20:
                parts = caller.split("0091", 1)
                caller = parts[0]
                extracted_did = "+91" + parts[1]

            # Extract dialed DID directly from SIP To header, request URI, Diversion, or packed from_hdr
            to_user = ""
            if to_hdr:
                raw_to = to_hdr.split("<")[1].split(">")[0] if "<" in to_hdr else to_hdr
                to_user = raw_to.replace("sip:", "").split("@")[0].split(";")[0].strip()

            diversion = invite_msg.get_header("diversion") or invite_msg.get_header("x-did") or ""
            if diversion and "<sip:" in diversion:
                extracted_did = diversion.split("<sip:")[1].split("@")[0].split(">")[0].strip()

            if extracted_did:
                called = extracted_did
            elif to_user and to_user not in ("unknown", "", self.username):
                called = to_user
            else:
                called = self.did_number or self.username

            _log_sip(f"\n📞 [AsteriskSIP] Answering Call: Caller={caller}, Called={called} (ToHeader: {to_hdr}, ExtractedDID={extracted_did}), Call-ID={call_id} (to_tag={to_tag})")

            # 1. Initialize WebRTC PeerConnection
            pc = RTCPeerConnection()
            self.active_calls[call_id] = pc

            @pc.on("iceconnectionstatechange")
            def on_ice():
                _log_sip(f"[AsteriskSIP] ICE Connection State for {call_id}: {pc.iceConnectionState}")

            @pc.on("connectionstatechange")
            def on_conn():
                _log_sip(f"[AsteriskSIP] PeerConnection State for {call_id}: {pc.connectionState}")

            outbound_track = OutboundAudioTrack()
            pc.addTrack(outbound_track)

            caps = RTCRtpSender.getCapabilities("audio")
            preferred_codecs = [c for c in caps.codecs if c.name == "PCMA"]
            for tr in pc.getTransceivers():
                try:
                    tr.setCodecPreferences(preferred_codecs)
                except Exception:
                    pass

            # 2. Handle incoming remote audio track
            fastapi_ws_task: Optional[asyncio.Task] = None

            @pc.on("track")
            def on_track(remote_track):
                nonlocal fastapi_ws_task
                if remote_track.kind == "audio":
                    _log_sip(f"[AsteriskSIP] Remote WebRTC audio track received for call {call_id}!")
                    fastapi_ws_task = asyncio.create_task(
                        self._bridge_webrtc_to_fastapi(call_id, caller, called, remote_track, outbound_track)
                    )

            # 3. Set remote SDP offer from Asterisk
            sdp_offer = invite_msg.body
            _log_sip(f"[AsteriskSIP] Asterisk SDP Offer:\n{sdp_offer}")
            offer = RTCSessionDescription(sdp=sdp_offer, type="offer")
            await pc.setRemoteDescription(offer)

            # 4. Generate local SDP answer
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            _log_sip(f"[AsteriskSIP] Local SDP Answer:\n{pc.localDescription.sdp}")

            # 5. Send SIP 200 OK with SDP Answer maintaining identical to_tag and Record-Route
            response_200 = create_200_ok_response(
                invite_msg,
                pc.localDescription.sdp,
                to_tag=to_tag,
                username=self.username,
                host=self.host
            )
            await self.ws.send(response_200)
            _log_sip(f"[AsteriskSIP] Sent 200 OK with WebRTC SDP Answer for call {call_id} (tag={to_tag})")

            # Save dialog metadata for terminating the call with SIP BYE
            self.active_dialogs[call_id] = {
                "from_header": from_hdr,
                "to_header": to_hdr,
                "to_tag": to_tag,
                "contact": invite_msg.get_header("contact"),
                "route": invite_msg.get_header("record-route")
            }
        except Exception as e:
            _log_sip(f"❌ [AsteriskSIP] Error in _handle_incoming_call for {call_id}: {e}")
            import traceback
            _log_sip(traceback.format_exc())

    async def _bridge_webrtc_to_fastapi(
        self,
        call_id: str,
        caller: str,
        called: str,
        remote_track,
        outbound_track: OutboundAudioTrack,
        answered_event: Optional[asyncio.Event] = None,
        tenant_id: Optional[str] = None
    ):
        """Bridges incoming WebRTC audio frames with local FastAPI /sip/stream WebSocket."""
        import urllib.parse

        # 1. For outbound calls, wait until customer actually answers (SIP 200 OK)
        if answered_event is not None:
            _log_sip(f"[AsteriskSIP] ⏳ Outbound call {call_id}: Waiting for customer to answer the phone (200 OK)...")
            ring_start = time.time()
            while not answered_event.is_set():
                if time.time() - ring_start > 60:
                    _log_sip(f"[AsteriskSIP] ⏱️ Call {call_id} timed out waiting for answer (60s ring timeout).")
                    await self.hangup_call(call_id)
                    return
                try:
                    # Drain carrier ringback / early media so WebRTC buffer doesn't stall
                    frame = await asyncio.wait_for(remote_track.recv(), timeout=0.25)
                except asyncio.TimeoutError:
                    if answered_event.is_set():
                        break
                    continue
                except MediaStreamError:
                    _log_sip(f"[AsteriskSIP] Call {call_id} ended before answer.")
                    return
                except Exception as e:
                    _log_sip(f"[AsteriskSIP] Early media drain ended: {e}")
                    break

            if not answered_event.is_set():
                _log_sip(f"[AsteriskSIP] Outbound call {call_id} was not answered.")
                return

            _log_sip(f"[AsteriskSIP] 📞 Customer answered! Starting AI conversational voice agent for {call_id}...")
            outbound_track.reset_timing()

        clean_call_id = urllib.parse.quote(str(call_id or "").strip())
        clean_caller = urllib.parse.quote(str(caller or "unknown").strip())
        clean_called = urllib.parse.quote(str(called or "").strip().replace(" ", ""))
        clean_tenant = urllib.parse.quote(str(tenant_id or "").strip())
        fastapi_url = f"ws://127.0.0.1:{settings.PORT}/sip/stream?call_id={clean_call_id}&caller={clean_caller}&called={clean_called}&tenant_id={clean_tenant}"
        try:
            _log_sip(f"[AsteriskSIP] Connecting to FastAPI WebSocket bridge: {fastapi_url}")
            bridge_url = f"{fastapi_url}&bridge_token={settings.INTERNAL_BRIDGE_TOKEN}"
            async with websockets.connect(bridge_url) as ws:
                _log_sip(f"[AsteriskSIP] ✅ FastAPI /sip/stream bridge connected successfully for call {call_id}!")
                self._fastapi_bridges[call_id] = ws
                # 1. Send start event
                await ws.send(json.dumps({
                    "event": "start",
                    "call_id": call_id,
                    "caller": caller,
                    "called": called,
                    "tenant_id": tenant_id
                }))

                # 2. Worker to read incoming speech from FastAPI and send to WebRTC track
                async def read_from_fastapi():
                    received_chunks = 0
                    total_bytes = 0
                    try:
                        async for message in ws:
                            data = json.loads(message)
                            evt = data.get("event")
                            if evt == "media":
                                pcm_bytes = base64.b64decode(data.get("media", {}).get("payload", ""))
                                if pcm_bytes:
                                    outbound_track.put_pcm(pcm_bytes)
                                    received_chunks += 1
                                    total_bytes += len(pcm_bytes)
                                    if received_chunks in (1, 10, 30, 60, 100):
                                        _log_sip(f"[AsteriskSIP] 🔊 Audio playback chunk {received_chunks} queued ({total_bytes} bytes so far)")
                            elif evt == "clear":
                                _log_sip(f"[AsteriskSIP] ⚡ Playback cleared (interrupted by caller barge-in)")
                                outbound_track.clear()
                            elif evt == "hangup":
                                _log_sip(f"[AsteriskSIP] 📴 Received hangup event from AI agent for call {call_id}. Waiting for audio queue to finish playing...")
                                # Wait for farewell speech to finish transmitting to WebRTC
                                while outbound_track and not outbound_track.queue.empty():
                                    await asyncio.sleep(0.05)
                                # Extra 400ms buffer for carrier network playout to caller's ear
                                await asyncio.sleep(0.4)
                                _log_sip(f"[AsteriskSIP] Outbound track drained. Hanging up call {call_id} now!")
                                await self.hangup_call(call_id)
                                break
                            elif evt == "transfer":
                                target_num = data.get("target_number") or settings.DEFAULT_ESCALATION_NUMBER
                                _log_sip(f"[AsteriskSIP] 📞 Received call transfer request from AI agent for call {call_id} -> Target: {target_num}")
                                # Wait for transfer announcement speech to finish playing to caller
                                while outbound_track and not outbound_track.queue.empty():
                                    await asyncio.sleep(0.05)
                                await asyncio.sleep(0.4)
                                # Execute SIP REFER
                                success = await self.transfer_call(call_id, target_num)
                                if success:
                                    _log_sip(f"[AsteriskSIP] ✅ SIP REFER dispatched for call {call_id}. Transferring to {target_num}...")
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        _log_sip(f"[AsteriskSIP] read_from_fastapi error: {e}")

                fastapi_reader = asyncio.create_task(read_from_fastapi())

                # 3. Stream incoming caller audio frames from WebRTC to FastAPI
                resampler = av.AudioResampler(format="s16", layout="mono", rate=8000)
                while True:
                    try:
                        frame = await remote_track.recv()
                        if not frame.sample_rate:
                            frame.sample_rate = 8000
                        resampled = resampler.resample(frame)
                        for r_frame in resampled:
                            raw_pcm = bytes(r_frame.planes[0])
                            payload = base64.b64encode(raw_pcm).decode("utf-8")
                            await ws.send(json.dumps({
                                "event": "media",
                                "media": {"payload": payload}
                            }))
                    except MediaStreamError:
                        _log_sip(f"[AsteriskSIP] Caller MediaStream ended for {call_id}")
                        break
                    except Exception as e:
                        _log_sip(f"❌ [AsteriskSIP] Error in audio frame processing for {call_id}: {e}")
                        import traceback
                        _log_sip(traceback.format_exc())
                        break

                if not fastapi_reader.done():
                    fastapi_reader.cancel()
        except Exception as e:
            _log_sip(f"❌ [AsteriskSIP] Error in WebRTC-FastAPI bridge: {e}")
            import traceback
            _log_sip(traceback.format_exc())
        finally:
            self._fastapi_bridges.pop(call_id, None)
            _log_sip(f"[AsteriskSIP] Bridge ended for call {call_id}")
            await self.hangup_call(call_id)

    async def make_outbound_call(
        self,
        target_phone: str,
        tenant_id: str = "idigitalstudies",
        caller_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Initiates an outbound SIP call via RP Digital Asterisk PBX with WebRTC AI voice bridge.
        """
        if not self.ws or not self.is_running or not self.is_registered:
            raise RuntimeError(f"RP Digital Asterisk client ({self.username}) is not currently registered.")

        clean_target = target_phone.strip().replace(" ", "").replace("-", "")
        digits = re.sub(r"\D", "", clean_target)
        # Indian PSTN carrier trunks in Asterisk PBX match 0 + 10 digits (e.g. 09630729848)
        if digits.startswith("91") and len(digits) == 12:
            phone_10 = digits[2:]
            dial_target = f"0{phone_10}"
            clean_target = f"+91{phone_10}"
        elif digits.startswith("0") and len(digits) == 11:
            phone_10 = digits[1:]
            dial_target = f"0{phone_10}"
            clean_target = f"+91{phone_10}"
        elif len(digits) == 10:
            phone_10 = digits
            dial_target = f"0{phone_10}"
            clean_target = f"+91{phone_10}"
        else:
            dial_target = clean_target.lstrip("+")

        call_id = f"sip_out_{uuid.uuid4().hex[:12]}"
        from_tag = uuid.uuid4().hex[:8]
        from_did = caller_id or self.did_number or self.username or "+917935486072"
        answered_event = asyncio.Event()
        early_media_event = asyncio.Event()

        _log_sip(f"\n📞 [AsteriskSIP] Placing Outbound Call to {clean_target} (dial: {dial_target}) via RP Digital DID {from_did} [Call-ID: {call_id}]")

        # 1. Initialize WebRTC PeerConnection for outbound call
        pc = RTCPeerConnection()
        self.active_calls[call_id] = pc

        outbound_track = OutboundAudioTrack()
        pc.addTrack(outbound_track)

        @pc.on("track")
        def on_track(remote_track):
            if remote_track.kind == "audio":
                _log_sip(f"[AsteriskSIP] 🎙️ Remote audio track received from customer {clean_target} for call {call_id}!")
                asyncio.create_task(
                    self._bridge_webrtc_to_fastapi(
                        call_id,
                        clean_target,
                        from_did,
                        remote_track,
                        outbound_track,
                        answered_event=answered_event,
                        tenant_id=tenant_id
                    )
                )

        @pc.on("iceconnectionstatechange")
        def on_ice():
            _log_sip(f"[AsteriskSIP] Outbound ICE State for {call_id}: {pc.iceConnectionState}")

        # 2. Prioritize PCMU (G.711 mu-law, payload 0) matching Asterisk's native outbound trunk codec
        caps = RTCRtpSender.getCapabilities("audio")
        preferred_codecs = [c for c in caps.codecs if c.name == "PCMU"] + [c for c in caps.codecs if c.name == "PCMA"]
        for tr in pc.getTransceivers():
            try:
                tr.setCodecPreferences(preferred_codecs)
            except Exception as e:
                _log_sip(f"[AsteriskSIP] Warning setting codec preferences: {e}")

        # 3. Create WebRTC SDP Offer
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        sdp_offer = pc.localDescription.sdp

        # 3. Store in outbound_calls
        self.outbound_calls[call_id] = {
            "call_id": call_id,
            "clean_target": clean_target,
            "dial_target": dial_target,
            "tenant_id": tenant_id,
            "pc": pc,
            "outbound_track": outbound_track,
            "sdp_offer": sdp_offer,
            "from_tag": from_tag,
            "cseq": 1,
            "answered_event": answered_event,
            "early_media_event": early_media_event,
            "failure_error": None,
            "created_at": time.time()
        }

        # 4. Send initial SIP INVITE
        invite_msg, _, _ = create_invite_request(
            from_user=self.username,
            to_number=dial_target,
            domain=self.host,
            port=self.port,
            sdp_offer=sdp_offer,
            call_id=call_id,
            cseq=1,
            from_tag=from_tag
        )
        await self.ws.send(invite_msg)
        _log_sip(f"[AsteriskSIP] Sent initial SIP INVITE for {dial_target} (Call-ID: {call_id})")

        # Wait briefly (up to 1.2s) to detect if Asterisk immediately rejects the call (e.g. 404/403/503)
        try:
            await asyncio.wait_for(early_media_event.wait(), timeout=1.2)
        except asyncio.TimeoutError:
            pass

        if self.outbound_calls.get(call_id, {}).get("failure_error"):
            err_msg = self.outbound_calls[call_id]["failure_error"]
            raise RuntimeError(f"Asterisk rejected call to {dial_target}: {err_msg}")

        return {
            "call_sid": call_id,
            "status": "calling",
            "to": clean_target,
            "from": from_did,
            "tenant_id": tenant_id,
            "vendor": "rpdigital"
        }


# ─────────────────────────────────────────────────────────────────────────────
# Multi-SIP Extension Registry & Manager
# Supports running concurrent WebRTC SIP connections for multiple tenant DIDs
# ─────────────────────────────────────────────────────────────────────────────
_SIP_CLIENT_INSTANCE: Optional[AsteriskSipClient] = None
_SIP_CLIENTS: Dict[str, AsteriskSipClient] = {}

def get_asterisk_sip_client(did_or_user: Optional[str] = None) -> AsteriskSipClient:
    """
    Returns the AsteriskSipClient for a specific DID number or SIP extension.
    If not specified or not found, falls back to the master default client.
    """
    global _SIP_CLIENT_INSTANCE, _SIP_CLIENTS
    if did_or_user:
        clean_key = str(did_or_user).strip()
        if clean_key in _SIP_CLIENTS:
            return _SIP_CLIENTS[clean_key]
        digits = re.sub(r"\D", "", clean_key)
        if digits in _SIP_CLIENTS:
            return _SIP_CLIENTS[digits]
        clean_plus = "+" + digits if digits else ""
        if clean_plus in _SIP_CLIENTS:
            return _SIP_CLIENTS[clean_plus]

    if _SIP_CLIENT_INSTANCE is None:
        _SIP_CLIENT_INSTANCE = AsteriskSipClient()
        if _SIP_CLIENT_INSTANCE.username:
            _SIP_CLIENTS[_SIP_CLIENT_INSTANCE.username] = _SIP_CLIENT_INSTANCE
        if _SIP_CLIENT_INSTANCE.did_number:
            _SIP_CLIENTS[_SIP_CLIENT_INSTANCE.did_number] = _SIP_CLIENT_INSTANCE
            clean_d = re.sub(r"\D", "", _SIP_CLIENT_INSTANCE.did_number)
            if clean_d:
                _SIP_CLIENTS[clean_d] = _SIP_CLIENT_INSTANCE
    return _SIP_CLIENT_INSTANCE


def get_all_sip_clients() -> List[AsteriskSipClient]:
    """Returns unique list of all instantiated Asterisk SIP clients."""
    seen = set()
    clients = []
    if _SIP_CLIENT_INSTANCE:
        seen.add(id(_SIP_CLIENT_INSTANCE))
        clients.append(_SIP_CLIENT_INSTANCE)
    for c in _SIP_CLIENTS.values():
        if id(c) not in seen:
            seen.add(id(c))
            clients.append(c)
    return clients


async def start_sip_client_for_credentials(
    username: str,
    password: str,
    did_number: str = "",
    host: Optional[str] = None,
    port: Optional[int] = None
) -> AsteriskSipClient:
    """Instantiates, registers, and starts a SIP client for a specific DID extension."""
    global _SIP_CLIENTS
    clean_user = username.strip()
    clean_pwd = password.strip() if password else clean_user
    clean_did = did_number.strip() if did_number else ""

    if clean_user in _SIP_CLIENTS and _SIP_CLIENTS[clean_user].is_running:
        return _SIP_CLIENTS[clean_user]

    client = AsteriskSipClient(
        host=host or getattr(settings, "RPDIGITAL_ASTERISK_HOST", "kenyavoice.rpdigitalphone.com"),
        port=port or getattr(settings, "RPDIGITAL_ASTERISK_PORT", 5000),
        username=clean_user,
        password=clean_pwd,
        did_number=clean_did
    )
    _SIP_CLIENTS[clean_user] = client
    if clean_did:
        _SIP_CLIENTS[clean_did] = client
        clean_d = re.sub(r"\D", "", clean_did)
        if clean_d:
            _SIP_CLIENTS[clean_d] = client

    asyncio.create_task(client.start())
    print(f"📞 [Multi-SIP Manager] Registered & launched SIP extension {clean_user} (DID: {clean_did or 'none'})")
    return client


async def start_all_sip_clients():
    """
    Launches all active SIP clients on server startup:
    1. Primary default client from settings / .env
    2. All active Phonotel/RPDigital SIP extensions configured in PostgreSQL
    """
    global _SIP_CLIENT_INSTANCE
    # 1. Master default client
    if settings.RPDIGITAL_SIP_USER and settings.RPDIGITAL_SIP_PASSWORD:
        master = get_asterisk_sip_client()
        if not master.is_running:
            asyncio.create_task(master.start())
            print(f"📞 [Multi-SIP Manager] Master SIP trunk launched: user={settings.RPDIGITAL_SIP_USER} (DID={settings.RPDIGITAL_DID_NUMBER})")

    # 2. Dynamic clients from database
    try:
        from app.db.session import sync_engine
        from sqlalchemy import text
        with sync_engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT DISTINCT number, sip_extension, COALESCE(sip_password, sip_extension) as pwd
                FROM phone_number_pool 
                WHERE vendor = 'rpdigital' 
                  AND sip_extension IS NOT NULL 
                  AND sip_extension != ''
                UNION
                SELECT DISTINCT number, sip_extension, COALESCE(sip_password, sip_extension) as pwd
                FROM tenant_phone_numbers 
                WHERE vendor = 'rpdigital' 
                  AND sip_extension IS NOT NULL 
                  AND sip_extension != ''
            """)).fetchall()

            from app.core.secrets_box import decrypt_secret
            for r in rows:
                num, ext, pwd = r[0], r[1], r[2]
                if ext and ext != getattr(settings, "RPDIGITAL_SIP_USER", ""):
                    try:
                        resolved_pwd = decrypt_secret(pwd) if pwd else ext
                        await start_sip_client_for_credentials(
                            username=ext,
                            password=resolved_pwd or ext,
                            did_number=num or ""
                        )
                    except Exception as ce:
                        print(f"⚠️ [Multi-SIP Manager] Error launching extension {ext}: {ce}")
    except Exception as e:
        print(f"⚠️ [Multi-SIP Manager] Database discovery warning: {e}")


async def stop_all_sip_clients():
    """Stops all running Asterisk SIP clients gracefully on shutdown."""
    clients = get_all_sip_clients()
    for c in clients:
        try:
            await c.stop()
        except Exception:
            pass
    print(f"🛑 [Multi-SIP Manager] Stopped {len(clients)} Asterisk SIP client(s).")


