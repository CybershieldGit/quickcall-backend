"""
Twilio Voice Webhook Router
Handles: Inbound call greeting, speech gather loop, live call escalation with transfer + lead fallback.
"""
from fastapi import APIRouter, Form, Response, Request
from twilio.twiml.voice_response import VoiceResponse, Gather, Dial
from app.core.voice_agent import VoiceAgent, SESSIONS, _resolve_tenant_from_did
from app.config import settings

router = APIRouter(prefix="/twilio", tags=["Twilio Voice"])


VOICE_NAME = "Polly.Kajal-Neural"


def _build_gather(action: str, timeout: int = 6) -> Gather:
    return Gather(
        input="speech",
        action=action,
        method="POST",
        timeout=timeout,
        speech_timeout="auto",
        speech_model="experimental_conversational",
        language="en-IN"
    )


@router.api_route("/voice", methods=["GET", "POST"])
async def twilio_inbound_call(
    CallSid: str = Form(default=""),
    Called: str = Form(default=""),
    Caller: str = Form(default="unknown"),
    To: str = Form(default=""),
    From_: str = Form(default="", alias="From"),
    Direction: str = Form(default="inbound")
):
    """
    Entry point for Twilio calls (both inbound and outbound-api).
    1. Resolves tenant from DID.
    2. Creates VoiceAgent session.
    3. Speaks greeting and listens for caller.
    """
    if Direction == "outbound-api" or Caller == settings.TWILIO_PHONE_NUMBER or From_ == settings.TWILIO_PHONE_NUMBER:
        customer_phone = Called or To
    else:
        customer_phone = Caller or From_

    tenant_id = _resolve_tenant_from_did(Called) if Called else "idigitalstudies"
    if not tenant_id:
        tenant_id = "idigitalstudies"

    agent = VoiceAgent(call_id=CallSid, tenant_id=tenant_id, caller_number=customer_phone)
    greeting = agent.get_greeting()

    response = VoiceResponse()
    gather = _build_gather(action="/twilio/gather", timeout=6)
    gather.say(greeting, voice=VOICE_NAME)
    response.append(gather)

    # Gentle reprompt instead of abrupt hangup
    reprompt = _build_gather(action="/twilio/gather", timeout=6)
    reprompt.say("I'm here to help! You can ask about our digital marketing modules, course fees, or live internship.", voice=VOICE_NAME)
    response.append(reprompt)

    response.say("Thank you for connecting with iDigitalStudies. Goodbye!", voice=VOICE_NAME)
    response.hangup()

    return Response(content=str(response), media_type="application/xml")


@router.post("/gather")
async def twilio_gather(
    CallSid: str = Form(...),
    SpeechResult: str = Form(default=""),
    Called: str = Form(default=""),
    Caller: str = Form(default="unknown"),
    To: str = Form(default=""),
    From_: str = Form(default="", alias="From"),
    Direction: str = Form(default="inbound")
):
    """
    Called each time Twilio recognises speech from the caller.
    Runs the agent turn and either continues conversation or triggers escalation.
    """
    if Direction == "outbound-api" or Caller == settings.TWILIO_PHONE_NUMBER or From_ == settings.TWILIO_PHONE_NUMBER:
        customer_phone = Called or To
    else:
        customer_phone = Caller or From_

    tenant_id = _resolve_tenant_from_did(Called) if Called else "idigitalstudies"
    if not tenant_id:
        tenant_id = "idigitalstudies"

    agent = VoiceAgent(call_id=CallSid, tenant_id=tenant_id, caller_number=customer_phone)
    user_speech = SpeechResult.strip() if SpeechResult else ""

    if not user_speech:
        # Caller was silent
        response = VoiceResponse()
        gather = _build_gather(action="/twilio/gather", timeout=6)
        gather.say("I didn't quite catch that. Could you please repeat your question?", voice=VOICE_NAME)
        response.append(gather)
        response.say("Thank you for connecting with iDigitalStudies. Have a wonderful day! Goodbye.", voice=VOICE_NAME)
        response.hangup()
        return Response(content=str(response), media_type="application/xml")

    print(f"[Twilio {CallSid}] Caller: '{user_speech}'")
    result = agent.process_turn(user_speech)
    agent_text = result["response"]
    is_final = result["is_final"]
    escalate = result.get("escalate", False)
    escalation_number = result.get("escalation_number", settings.DEFAULT_ESCALATION_NUMBER)

    print(f"[Twilio {CallSid}] Agent: '{agent_text}' | escalate={escalate} | final={is_final}")

    response = VoiceResponse()

    if escalate:
        # Speak hold message then attempt live transfer
        response.say(agent_text, voice=VOICE_NAME)
        dial = Dial(timeout=30, action=f"{settings.BASE_URL}/twilio/transfer-result", method="POST")
        dial.number(escalation_number)
        response.append(dial)

    elif is_final:
        response.say(agent_text, voice=VOICE_NAME)
        response.hangup()

    else:
        gather = _build_gather(action="/twilio/gather", timeout=6)
        gather.say(agent_text, voice=VOICE_NAME)
        response.append(gather)

        # Gentle reprompt if user doesn't speak right away
        reprompt_gather = _build_gather(action="/twilio/gather", timeout=6)
        reprompt_gather.say("I'm still here. Feel free to ask anything about our digital marketing course or batches.", voice=VOICE_NAME)
        response.append(reprompt_gather)

        response.say("Thank you for connecting with iDigitalStudies. Goodbye!", voice=VOICE_NAME)
        response.hangup()

    return Response(content=str(response), media_type="application/xml")


@router.post("/transfer-result")
async def twilio_transfer_result(
    CallSid: str = Form(...),
    DialCallStatus: str = Form(default=""),
    Called: str = Form(default=""),
    Caller: str = Form(default="unknown")
):
    """
    Called by Twilio after a transfer attempt with the outcome (completed, no-answer, busy, failed).
    """
    tenant_id = _resolve_tenant_from_did(Called) if Called else "idigitalstudies"
    if not tenant_id:
        tenant_id = "idigitalstudies"

    agent = VoiceAgent(call_id=CallSid, tenant_id=tenant_id, caller_number=Caller)
    response = VoiceResponse()

    print(f"[Twilio Transfer {CallSid}] DialCallStatus={DialCallStatus}")

    if DialCallStatus in ("completed", "in-progress"):
        agent.mark_transferred()
        response.say("You have been connected. Goodbye!", voice=VOICE_NAME)
        response.hangup()
    else:
        agent.mark_escalation_failed()
        result = agent.process_turn("transfer failed, start lead capture")
        fallback_text = result["response"]

        gather = _build_gather(action="/twilio/gather", timeout=6)
        gather.say(fallback_text, voice=VOICE_NAME)
        response.append(gather)
        response.say("Thank you. We will get back to you shortly. Goodbye!", voice=VOICE_NAME)
        response.hangup()

    return Response(content=str(response), media_type="application/xml")


@router.post("/status")
async def twilio_status_callback(
    CallSid: str = Form(default=""),
    CallStatus: str = Form(default=""),
    Called: str = Form(default=""),
    Caller: str = Form(default="")
):
    """
    Twilio call status callback (ringing, answered, completed, busy, etc.)
    """
    print(f"[Twilio Status {CallSid}] Status: {CallStatus} | Called: {Called} | Caller: {Caller}")
    return {"status": "received", "call_sid": CallSid, "call_status": CallStatus}

