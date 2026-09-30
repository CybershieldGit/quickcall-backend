#!/usr/bin/env python3
"""
Trigger Outbound AI Voice Call
Usage:
    python3 make_call.py [phone_number]

Example:
    python3 make_call.py 6265797401
    python3 make_call.py +916265797401
"""
import sys
import os
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Gather
from dotenv import load_dotenv

# Load env variables
script_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(script_dir, '.env'))

sid = os.getenv("TWILIO_ACCOUNT_SID")
token = os.getenv("TWILIO_AUTH_TOKEN")
from_num = os.getenv("TWILIO_PHONE_NUMBER")
base_url = os.getenv("BASE_URL")

if not sid or not token or not from_num:
    print("❌ Error: TWILIO credentials missing in .env")
    sys.exit(1)

# Target phone number
target = sys.argv[1] if len(sys.argv) > 1 else "6265797401"
target = target.strip()
if not target.startswith("+"):
    if len(target) == 10:
        target = "+91" + target
    else:
        target = "+" + target

print(f"📞 Calling {target} from {from_num}...")
print(f"🌐 Tunnel Webhook: {base_url}")

# Build initial greeting TwiML with lifelike Indian Neural voice
response = VoiceResponse()
gather = Gather(
    input="speech",
    action=f"{base_url}/twilio/gather",
    method="POST",
    timeout=6,
    speech_timeout="auto",
    speech_model="experimental_conversational",
    language="en-IN"
)
gather.say(
    "Hello! Welcome to iDigitalStudies. I'm Aanya, your admissions advisor. How can I help you today? You can ask me about our digital marketing course, fees, batch timings, or our live internship.",
    voice="Polly.Kajal-Neural"
)
response.append(gather)

# Gentle reprompt if caller doesn't speak right away
reprompt = Gather(
    input="speech",
    action=f"{base_url}/twilio/gather",
    method="POST",
    timeout=6,
    speech_timeout="auto",
    speech_model="experimental_conversational",
    language="en-IN"
)
reprompt.say("I'm here to help! Feel free to ask anything about our curriculum, fees, or internship.", voice="Polly.Kajal-Neural")
response.append(reprompt)

response.say("Thank you for connecting with iDigitalStudies. Goodbye!", voice="Polly.Kajal-Neural")
response.hangup()

client = Client(sid, token)

try:
    call = client.calls.create(
        to=target,
        from_=from_num,
        twiml=str(response),
        status_callback=f"{base_url}/twilio/status",
        status_callback_event=["initiated", "ringing", "answered", "completed"],
        status_callback_method="POST"
    )
    print(f"✅ Call placed successfully!")
    print(f"🆔 Call SID: {call.sid}")
    print(f"📊 Status: {call.status}")
except Exception as e:
    print(f"❌ Failed to place call: {e}")
