"""
Enterprise Exotel Voice Webhook Router
Handles:
- Voicebot dynamic connect endpoint
- Passthru applet webhooks
- Call status callbacks
- Transfer bridge outcome handling
"""
import json
from fastapi import APIRouter, Request, Response, Form, Query, BackgroundTasks
from typing import Optional
from app.config import settings
from app.core.voice_agent import (
    _resolve_tenant_from_did, _log_call_end, generate_executive_summary, SESSIONS
)

router = APIRouter(prefix="/exotel", tags=["Exotel Voice Webhooks"])


@router.api_route("/voicebot-connect", methods=["GET", "POST"])
async def voicebot_dynamic_connect(request: Request):
    """
    Dynamic endpoint called by Exotel Voicebot Applet.
    Returns the WebSocket endpoint for full-duplex PCM streaming.
    """
    host = request.headers.get("host") or settings.BASE_URL.replace("https://", "").replace("http://", "")
    proto = "wss" if ("https" in settings.BASE_URL or request.headers.get("x-forwarded-proto") == "https") else "ws"
    
    # Query / Form params
    query = request.query_params
    call_sid = query.get("CallSid") or query.get("call_sid") or ""
    caller = query.get("From") or query.get("caller") or query.get("Caller") or ""
    called = query.get("To") or query.get("called") or query.get("Called") or ""

    try:
        form = await request.form()
        call_sid = call_sid or form.get("CallSid") or form.get("call_sid") or ""
        caller = caller or form.get("From") or form.get("caller") or form.get("Caller") or ""
        called = called or form.get("To") or form.get("called") or form.get("Called") or ""
    except Exception:
        pass

    ws_url = f"{proto}://{host}/exotel/stream?call_sid={call_sid}&caller={caller}&called={called}"
    
    return {
        "url": ws_url,
        "sample_rate": 8000,
        "format": "slin16"
    }


@router.api_route("/passthru", methods=["GET", "POST"])
async def exotel_passthru(
    request: Request,
    CallSid: Optional[str] = Form(default=None),
    From: Optional[str] = Form(default=None),
    To: Optional[str] = Form(default=None),
    Direction: Optional[str] = Form(default="inbound"),
    CallType: Optional[str] = Form(default=None)
):
    """
    Exotel Passthru Applet callback endpoint.
    Handles post-Voicebot dynamic escalation routing:
    - Returns HTTP 200 OK if transfer/escalation was requested (routes to Connect applet in Exotel).
    - Returns HTTP 400 Bad Request if call completed normally (routes to Hangup applet in Exotel).
    """
    call_sid = CallSid or request.query_params.get("CallSid", "")
    caller = From or request.query_params.get("From", "unknown")
    called = To or request.query_params.get("To", "")

    print(f"[ExotelPassthru] Inbound call received: CallSid={call_sid}, From={caller}, To={called}")

    # Robust multi-key lookup in SESSIONS (by call_sid, caller phone variants, and recent escalation fallback)
    sess = SESSIONS.get(call_sid)
    if not sess and caller and caller != "unknown":
        c_clean = caller.strip()
        c_no_91 = c_clean.replace("+91", "").lstrip("0")
        sess = (
            SESSIONS.get(c_clean)
            or SESSIONS.get(f"+91{c_no_91}")
            or SESSIONS.get(f"0{c_no_91}")
            or SESSIONS.get(c_no_91)
        )

    # Fallback to recent session with escalation requested
    if not sess or not (sess.get("transfer_requested") or sess.get("escalated")):
        for s_id, s_data in reversed(list(SESSIONS.items())):
            if isinstance(s_data, dict) and (s_data.get("transfer_requested") or s_data.get("escalated")):
                sess = s_data
                print(f"[ExotelPassthru] Matched recent escalated session via fallback: key={s_id}")
                break

    if sess and (sess.get("transfer_requested") or sess.get("escalated") or sess.get("state") == "TRANSFERRED"):
        esc_num = sess.get("escalation_number") or settings.DEFAULT_ESCALATION_NUMBER
        print(f"[ExotelPassthru] Call {call_sid} flagged for escalation -> Returning HTTP 200 for Connect applet ({esc_num})")
        # Clear flag so subsequent calls don't accidentally re-trigger
        sess["transfer_requested"] = False
        sess["escalated"] = False
        return Response(
            content=json.dumps({
                "status": "success",
                "action": "transfer",
                "escalate": True,
                "transfer_to": esc_num,
                "counselor_number": esc_num
            }),
            media_type="application/json",
            status_code=200
        )

    # If call completed normally or no transfer was requested -> Return 400 so Exotel takes the "anything else" (Hangup) branch
    print(f"[ExotelPassthru] Call {call_sid} completed normally (no escalation) -> Returning HTTP 400 for Hangup branch")
    return Response(
        content=json.dumps({
            "status": "completed",
            "action": "hangup",
            "escalate": False
        }),
        media_type="application/json",
        status_code=400
    )


@router.post("/status")
async def exotel_status_callback(
    background_tasks: BackgroundTasks,
    CallSid: Optional[str] = Form(default=None),
    Status: Optional[str] = Form(default=None),
    From: Optional[str] = Form(default=None),
    Duration: Optional[int] = Form(default=0),
    RecordingUrl: Optional[str] = Form(default=None)
):
    """
    Handles call status updates from Exotel (completed, busy, failed, no-answer).
    Preserves transferred status and runs background LLM executive summary generation.
    """
    print(f"[ExotelStatus] CallSid={CallSid}, Status={Status}, From={From}, Duration={Duration}s")

    try:
        if CallSid:
            if From and From.strip() and From.strip().lower() != "unknown":
                from app.db.session import sync_engine
                from sqlalchemy import text
                try:
                    with sync_engine.connect() as conn:
                        conn.execute(
                            text("UPDATE calls SET caller_number = :caller WHERE call_id = :cid AND (caller_number IS NULL OR caller_number = '' OR caller_number = 'unknown')"),
                            {"caller": From.strip(), "cid": CallSid}
                        )
                        conn.commit()
                except Exception as dbe:
                    print(f"[ExotelStatus DB Update Error] {dbe}")

            sess = SESSIONS.get(CallSid, {})
            was_transferred = sess.get("transfer_requested", False) or sess.get("transfer_accepted", False)
            outcome = "transferred" if was_transferred else ("completed" if Status == "completed" else (Status or "ended"))
            try:
                _log_call_end(CallSid, outcome, was_transferred)
            except Exception as le:
                print(f"[ExotelStatus] Error in _log_call_end: {le}")

            try:
                background_tasks.add_task(generate_executive_summary, CallSid, outcome=outcome)
            except Exception as se:
                print(f"[ExotelStatus] Error queueing executive summary: {se}")
    except Exception as e:
        print(f"[ExotelStatus Error] {e}")

    return Response(content="OK", media_type="text/plain")


@router.post("/transfer-callback")
async def exotel_transfer_callback(
    CallSid: Optional[str] = Form(default=None),
    DialCallStatus: Optional[str] = Form(default=None),
    DialCallDuration: Optional[int] = Form(default=0)
):
    """
    Receives callback when Exotel attempts to bridge caller to counselor line.
    If counselor line is busy, no-answer, or failed, automatically saves an
    urgent callback lead in the leads table so the team can call back immediately!
    """
    print(f"[ExotelTransfer] CallSid={CallSid}, Status={DialCallStatus}, Duration={DialCallDuration}s")

    try:
        if CallSid:
            sess = SESSIONS.get(CallSid, {})
            tenant_id = sess.get("tenant_id") or "idigitalstudies"
            target_num = sess.get("escalation_number") or settings.DEFAULT_ESCALATION_NUMBER
            caller_num = sess.get("caller_number") or "unknown"
            caller_name = sess.get("lead_name") or "Inbound Caller"

            if DialCallStatus in ("completed", "answered"):
                sess["transfer_accepted"] = True
                sess["state"] = "COMPLETED"
                try:
                    _log_call_end(CallSid, "transferred", True)
                except Exception as le:
                    print(f"[ExotelTransfer] Error logging transfer call end: {le}")
                print(f"[ExotelTransfer] Call {CallSid} successfully bridged to {target_num} ({DialCallDuration}s)")
            else:
                # Counselor line was busy, unanswered, or failed
                sess["transfer_accepted"] = False
                try:
                    _log_call_end(CallSid, "escalation_failed_lead_captured", False)
                except Exception as le:
                    print(f"[ExotelTransfer] Error logging failed transfer: {le}")

                # Auto-save urgent lead record
                try:
                    from app.core.call_logger import log_lead
                    urgent_reason = f"🚨 URGENT CALLBACK: Counselor line {target_num} was {DialCallStatus or 'busy'}. Caller sought live assistance."
                    log_lead(
                        tenant_id=tenant_id,
                        call_id=CallSid,
                        name=caller_name,
                        phone=caller_num,
                        reason=urgent_reason
                    )
                    print(f"[ExotelTransfer] Logged urgent callback lead for {caller_num} (counselor was {DialCallStatus})")
                except Exception as lde:
                    print(f"[ExotelTransfer] Error saving lead: {lde}")
    except Exception as e:
        print(f"[ExotelTransfer Error] {e}")

    return Response(content="OK", media_type="text/plain")
