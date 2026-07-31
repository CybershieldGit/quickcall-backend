from fastapi import APIRouter, Form, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from app.core.interview_agent import InterviewAgent, SESSIONS
from app.config import settings

router = APIRouter(prefix="/twilio", tags=["Twilio Voice"])

@router.post("/voice")
async def twilio_voice_webhook(CallSid: str = Form(...)):
    """
    Twilio incoming call webhook.
    Greets the candidate and starts speech recognition.
    """
    agent = InterviewAgent(session_id=CallSid)
    greeting = agent.get_greeting()

    response = VoiceResponse()
    gather = Gather(
        input="speech",
        action="/twilio/gather",
        method="POST",
        speech_timeout="1",
        speech_model="experimental_conversational",
        language="en-US"
    )
    gather.say(greeting, voice="Polly.Joanna")
    response.append(gather)
    
    # Fallback if no speech detected
    response.say("We did not hear any speech. Thank you for calling QwikCall. Goodbye!", voice="Polly.Joanna")
    response.hangup()

    return Response(content=str(response), media_type="application/xml")


@router.post("/gather")
async def twilio_gather_webhook(
    CallSid: str = Form(...),
    SpeechResult: str = Form(default="")
):
    """
    Twilio webhook called when speech is recognized from candidate.
    """
    agent = InterviewAgent(session_id=CallSid)
    
    if not SpeechResult:
        user_speech = "No speech detected"
    else:
        user_speech = SpeechResult

    print(f"[Twilio CallSid: {CallSid}] User said: '{user_speech}'")
    
    # Process turn with LLM agent
    result = agent.process_turn(user_speech)
    agent_text = result["response"]
    is_final = result["is_final"]

    print(f"[Twilio CallSid: {CallSid}] Agent response: '{agent_text}'")

    response = VoiceResponse()

    if is_final:
        response.say(agent_text, voice="Polly.Joanna")
        response.hangup()
    else:
        gather = Gather(
            input="speech",
            action="/twilio/gather",
            method="POST",
            speech_timeout="1",
            speech_model="experimental_conversational",
            language="en-US"
        )
        gather.say(agent_text, voice="Polly.Joanna")
        response.append(gather)
        
        # Fallback if no speech detected
        response.say("Thank you for trying QwikCall. Goodbye!", voice="Polly.Joanna")
        response.hangup()

    return Response(content=str(response), media_type="application/xml")
