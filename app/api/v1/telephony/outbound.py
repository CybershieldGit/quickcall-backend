import os
import httpx
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.db.session import get_db

router = APIRouter(prefix="/api/call", tags=["Outbound Calls"])

def _get_current_base_url() -> str:
    """Dynamically resolve the active BASE_URL from local cloudflared or settings."""
    try:
        import urllib.request
        import json
        req = urllib.request.urlopen("http://127.0.0.1:20241/quicktunnel", timeout=0.5)
        data = json.loads(req.read().decode())
        hostname = data.get("hostname")
        if hostname:
            return f"https://{hostname}"
    except Exception:
        pass

    base = getattr(settings, "BASE_URL", "") or os.getenv("BASE_URL", "http://localhost:8001")
    return base.strip().rstrip("/")


class OutboundCallRequest(BaseModel):
    phone_number: Optional[str] = None
    to_number: Optional[str] = None
    tenant_id: Optional[str] = "idigitalstudies"
    stream_mode: Optional[bool] = True

def _clean_phone_number(raw: str) -> str:
    cleaned = raw.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if not cleaned.startswith("+"):
        if len(cleaned) == 10:
            cleaned = "+91" + cleaned
        else:
            cleaned = "+" + cleaned
    return cleaned

def trigger_exotel_outbound_call(target_number: str, tenant_id: str = "idigitalstudies") -> Dict[str, Any]:
    """
    Triggers an outbound call to the target phone number using Exotel REST API.
    Connects the caller to the Voicebot flow for full-duplex conversational AI.
    """
    if not settings.EXOTEL_ACCOUNT_SID or not settings.EXOTEL_API_KEY or not settings.EXOTEL_API_TOKEN:
        raise ValueError("EXOTEL_ACCOUNT_SID, EXOTEL_API_KEY, and EXOTEL_API_TOKEN must be configured in .env")
    if not settings.EXOTEL_CALLER_ID:
        raise ValueError("EXOTEL_CALLER_ID must be configured in .env")

    base_url = _get_current_base_url()
    cleaned = _clean_phone_number(target_number)

    url = f"https://{settings.EXOTEL_API_KEY}:{settings.EXOTEL_API_TOKEN}@{settings.EXOTEL_SUBDOMAIN}/v1/Accounts/{settings.EXOTEL_ACCOUNT_SID}/Calls/connect.json"
    
    payload = {
        "From": cleaned,
        "CallerId": settings.EXOTEL_CALLER_ID,
        "Url": f"{base_url}/exotel/passthru?tenant_id={tenant_id}",
        "StatusCallback": f"{base_url}/exotel/status",
        "CustomField": f"outbound_voicebot_{tenant_id}"
    }

    if settings.EXOTEL_APP_ID:
        payload["AppId"] = settings.EXOTEL_APP_ID

    try:
        with httpx.Client(timeout=10.0) as client:
            res = client.post(url, data=payload)
            if res.status_code in (200, 201):
                data = res.json()
                call_data = data.get("Call", {})
                return {
                    "call_sid": call_data.get("Sid", "exo_call"),
                    "status": call_data.get("Status", "queued"),
                    "to": cleaned,
                    "from": settings.EXOTEL_CALLER_ID,
                    "tenant_id": tenant_id,
                    "vendor": "exotel"
                }
            else:
                err_msg = res.text
                try:
                    err_data = res.json()
                    if "RestException" in err_data and "Message" in err_data["RestException"]:
                        err_msg = err_data["RestException"]["Message"]
                except Exception:
                    pass
                raise HTTPException(
                    status_code=res.status_code if 400 <= res.status_code < 500 else 502,
                    detail=f"Exotel Telecom Gateway ({res.status_code}): {err_msg}"
                )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Exotel Telecom Gateway timed out while placing outbound call.")
    except httpx.RequestError as re:
        raise HTTPException(status_code=502, detail=f"Network error connecting to Exotel Gateway: {str(re)}")

from app.core.auth import get_current_tenant_auth
import re

@router.post("/outbound")
@router.post("/make-call")
async def make_outbound_call(
    req: OutboundCallRequest,
    auth: Dict[str, Any] = Depends(get_current_tenant_auth)
):
    target = (req.phone_number or req.to_number or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="Phone number (phone_number or to_number) is required.")

    # Validate phone number length and format against toll-fraud injection
    digits = re.sub(r"\D", "", target)
    if (len(digits) < 4 or len(digits) > 15) and not target.startswith("*"):
        raise HTTPException(status_code=400, detail="Invalid phone number format. Must contain 4 to 15 digits.")

    tenant_id = req.tenant_id or auth.get("tenant_id") or "idigitalstudies"

    # Pre-call financial check: Ensure tenant has minimum credit balance
    from app.services.tenant.tenant_service import check_tenant_wallet_balance
    bal_ok, curr_bal, bal_msg = check_tenant_wallet_balance(tenant_id, min_required=15.0)
    if not bal_ok:
        raise HTTPException(
            status_code=402,
            detail=f"Call rejected: {bal_msg}. Please top up your wallet in the portal to continue."
        )

    from app.core.audio.sip_client import get_asterisk_sip_client
    sip_client = get_asterisk_sip_client()

    # 1. Primary: If RP Digital Asterisk SIP client is registered, dispatch via Asterisk WebRTC
    if sip_client.is_registered and sip_client.is_running:
        try:
            call_info = await sip_client.make_outbound_call(target, tenant_id=tenant_id)
            from_num = call_info.get("from") or settings.RPDIGITAL_DID_NUMBER or "+917935486072"
            return {
                "status": "success",
                "message": f"Calling {target} via RP Digital DID {from_num}...",
                "call_sid": call_info.get("call_sid"),
                "vendor": "rpdigital",
                "data": call_info
            }
        except Exception as e:
            print(f"[Outbound] Asterisk dispatch error: {e}, checking Exotel fallback...")

    # 2. Fallback / BYOC: If Exotel credentials exist, dispatch via Exotel Voicebot API
    if settings.EXOTEL_ACCOUNT_SID and settings.EXOTEL_API_KEY and settings.EXOTEL_API_TOKEN:
        try:
            call_info = trigger_exotel_outbound_call(target, tenant_id=tenant_id)
            return {
                "status": "success",
                "message": f"Calling {target} via Exotel Gateway ({settings.EXOTEL_CALLER_ID})...",
                "call_sid": call_info.get("call_sid"),
                "vendor": "exotel",
                "data": call_info
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to dispatch Exotel call: {str(e)}")

    raise HTTPException(
        status_code=400,
        detail=f"No active telephony gateway available. Asterisk client is not registered ({settings.RPDIGITAL_ASTERISK_HOST}) and Exotel credentials are not configured."
    )

@router.get("/recent")
@router.get("/active-calls")
async def get_active_calls(
    tenant_id: Optional[str] = Query(None, description="Optional tenant ID to filter call logs"),
    db: AsyncSession = Depends(get_db)
):
    """Returns the most recent calls from Postgres database asynchronously, strictly filtered by tenant when provided."""
    try:
        if tenant_id and tenant_id.strip():
            tid = tenant_id.strip()
            result = await db.execute(text("""
                SELECT call_id, caller_number, start_time, end_time, duration, outcome_tag, transferred,
                       summary, primary_concern, caller_sentiment, action_required, executive_notes
                FROM calls 
                WHERE tenant_id = :tid 
                   OR tenant_id IN (
                       SELECT t2.tenant_id FROM tenants t2 
                       WHERE t2.business_name ILIKE (SELECT t1.business_name FROM tenants t1 WHERE t1.tenant_id = :tid LIMIT 1)
                   )
                ORDER BY start_time DESC 
                LIMIT 25
            """), {"tid": tid})
        else:
            result = await db.execute(text("""
                SELECT call_id, caller_number, start_time, end_time, duration, outcome_tag, transferred,
                       summary, primary_concern, caller_sentiment, action_required, executive_notes
                FROM calls 
                ORDER BY start_time DESC 
                LIMIT 25
            """))

        rows = result.fetchall()
        calls = [
            {
                "call_id": r[0],
                "caller_number": r[1],
                "start_time": str(r[2]) if r[2] else None,
                "end_time": str(r[3]) if r[3] else None,
                "duration": r[4],
                "outcome": r[5] or "completed",
                "transferred": bool(r[6]),
                "summary": r[7],
                "primary_concern": r[8],
                "caller_sentiment": r[9],
                "action_required": r[10],
                "executive_notes": r[11]
            }
            for r in rows
        ]
        return {"calls": calls}
    except Exception as e:
        return {"calls": [], "error": str(e)}

@router.get("/{call_id}/transcript")
async def get_call_transcript(call_id: str, db: AsyncSession = Depends(get_db)):
    """Fetches full recorded turn-by-turn conversation dialogue for a specific call."""
    try:
        call_res = await db.execute(text("""
            SELECT call_id, caller_number, tenant_id, start_time, end_time, duration,
                   outcome_tag, transferred, summary, primary_concern, caller_sentiment,
                   action_required, executive_notes
            FROM calls
            WHERE call_id = :cid
        """), {"cid": call_id})
        call_row = call_res.fetchone()

        metadata = None
        if call_row:
            metadata = {
                "call_id": call_row[0],
                "caller_number": call_row[1] or "Unknown",
                "tenant_id": call_row[2],
                "start_time": str(call_row[3]) if call_row[3] else None,
                "end_time": str(call_row[4]) if call_row[4] else None,
                "duration": round(float(call_row[5] or 0.0), 1),
                "outcome": call_row[6] or "completed",
                "transferred": bool(call_row[7]),
                "summary": call_row[8],
                "primary_concern": call_row[9],
                "caller_sentiment": call_row[10] or "neutral",
                "action_required": call_row[11],
                "executive_notes": call_row[12]
            }

        transcripts_res = await db.execute(text("""
            SELECT turn_index, speaker, text, timestamp
            FROM call_transcripts
            WHERE call_id = :cid
            ORDER BY turn_index ASC
        """), {"cid": call_id})
        transcripts_rows = transcripts_res.fetchall()

        transcripts = [
            {
                "turn_index": r[0],
                "speaker": r[1],
                "text": r[2],
                "timestamp": str(r[3]) if r[3] else None
            }
            for r in transcripts_rows
        ]

        return {
            "status": "success",
            "call_id": call_id,
            "metadata": metadata,
            "total_turns": len(transcripts),
            "transcripts": transcripts
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load transcript: {str(e)}")

