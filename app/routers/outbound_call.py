from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from twilio.rest import Client
from app.config import settings

router = APIRouter(prefix="/api/call", tags=["Outbound Calls"])

class OutboundCallRequest(BaseModel):
    phone_number: str

def trigger_outbound_call(target_number: str) -> str:
    """
    Triggers an outbound call from Twilio phone number to the target mobile number,
    connecting it to the /twilio/voice webhook.
    """
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        raise ValueError("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be configured in .env")
    if not settings.TWILIO_PHONE_NUMBER:
        raise ValueError("TWILIO_PHONE_NUMBER must be configured in .env")

    client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    webhook_url = f"{settings.BASE_URL}/twilio/voice"

    call = client.calls.create(
        to=target_number.strip(),
        from_=settings.TWILIO_PHONE_NUMBER,
        url=webhook_url
    )
    return call.sid

@router.post("/outbound")
async def make_outbound_call(req: OutboundCallRequest):
    if not req.phone_number or not req.phone_number.strip():
        raise HTTPException(status_code=400, detail="Phone number is required.")

    try:
        call_sid = trigger_outbound_call(req.phone_number)
        return {
            "status": "success",
            "call_sid": call_sid,
            "message": f"Initiated outbound call to {req.phone_number}! Check your mobile phone."
        }
    except Exception as e:
        print(f"[OutboundCall Error]: {e}")
        raise HTTPException(status_code=400, detail=str(e))
