import os
import httpx
from fastapi import APIRouter, HTTPException, Depends, Query
from urllib.parse import quote
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.db.session import get_db
from app.core.auth import get_current_tenant_auth, verify_tenant_access, resolve_tenant_scope, tenant_alias_ids
from app.core.rate_limit import enforce_rate_limit
from app.services.tenant.tenant_service import get_tenant_did
from sqlalchemy import bindparam

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
    tenant_id: Optional[str] = None
    stream_mode: Optional[bool] = True

def _clean_phone_number(raw: str) -> str:
    cleaned = raw.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if not cleaned.startswith("+"):
        if len(cleaned) == 10:
            cleaned = "+91" + cleaned
        else:
            cleaned = "+" + cleaned
    return cleaned

def trigger_exotel_outbound_call(target_number: str, tenant_id: str = "idigitalstudies", caller_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Triggers an outbound call to the target phone number using Exotel REST API.
    Connects the caller to the Voicebot flow for full-duplex conversational AI.
    """
    if not settings.EXOTEL_ACCOUNT_SID or not settings.EXOTEL_API_KEY or not settings.EXOTEL_API_TOKEN:
        raise ValueError("EXOTEL_ACCOUNT_SID, EXOTEL_API_KEY, and EXOTEL_API_TOKEN must be configured in .env")

    effective_caller_id = caller_id or get_tenant_did(tenant_id, vendor="exotel") or settings.EXOTEL_CALLER_ID
    if not effective_caller_id:
        raise ValueError("No Exotel Caller ID configured in .env or assigned to tenant")

    base_url = _get_current_base_url()
    cleaned = _clean_phone_number(target_number)

    url = f"https://{settings.EXOTEL_SUBDOMAIN}/v1/Accounts/{settings.EXOTEL_ACCOUNT_SID}/Calls/connect.json"
    token_qs = f"&token={quote(settings.EXOTEL_WEBHOOK_TOKEN)}" if settings.EXOTEL_WEBHOOK_TOKEN else ""

    payload = {
        "From": cleaned,
        "CallerId": effective_caller_id,
        "Url": f"{base_url}/exotel/passthru?tenant_id={quote(tenant_id)}{token_qs}",
        "StatusCallback": f"{base_url}/exotel/status" + (f"?token={quote(settings.EXOTEL_WEBHOOK_TOKEN)}" if settings.EXOTEL_WEBHOOK_TOKEN else ""),
        "CustomField": f"outbound_voicebot_{tenant_id}"
    }

    if settings.EXOTEL_APP_ID:
        payload["AppId"] = settings.EXOTEL_APP_ID

    try:
        with httpx.Client(timeout=10.0) as client:
            res = client.post(url, data=payload, auth=(settings.EXOTEL_API_KEY, settings.EXOTEL_API_TOKEN))
            if res.status_code in (200, 201):
                data = res.json()
                call_data = data.get("Call", {})
                return {
                    "call_sid": call_data.get("Sid", "exo_call"),
                    "status": call_data.get("Status", "queued"),
                    "to": cleaned,
                    "from": effective_caller_id,
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
        raise HTTPException(status_code=502, detail="Network error connecting to Exotel Gateway.")

import re

_DIALABLE_RE = re.compile(r"^\+?[0-9]{10,15}$")

@router.post("/outbound")
@router.post("/make-call")
async def make_outbound_call(
    req: OutboundCallRequest,
    auth: Dict[str, Any] = Depends(get_current_tenant_auth)
):
    target = (req.phone_number or req.to_number or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="Phone number (phone_number or to_number) is required.")

    # Only plain 10-15 digit numbers are dialable (no star codes, SIP URIs, or short codes: toll-fraud guard)
    target = re.sub(r"[\s\-\(\)]", "", target)
    if not _DIALABLE_RE.match(target):
        raise HTTPException(status_code=400, detail="Invalid phone number format. Must be a 10 to 15 digit number.")

    tenant_id = resolve_tenant_scope(auth, req.tenant_id)
    enforce_rate_limit(f"outbound_call:{tenant_id}", limit=30, window_seconds=3600)

    # Pre-call financial check: Ensure tenant has minimum credit balance
    from app.services.tenant.tenant_service import check_tenant_wallet_balance
    bal_ok, curr_bal, bal_msg = check_tenant_wallet_balance(tenant_id, min_required=15.0)
    if not bal_ok:
        raise HTTPException(
            status_code=402,
            detail=f"Call rejected: {bal_msg}. Please top up your wallet in the portal to continue."
        )

    from app.core.audio.sip_client import get_asterisk_sip_client
    tenant_did = get_tenant_did(tenant_id, vendor="rpdigital") or settings.RPDIGITAL_DID_NUMBER or "+917935486072"
    sip_client = get_asterisk_sip_client(tenant_did)

    # 1. Primary: If RP Digital Asterisk SIP client is registered, dispatch via Asterisk WebRTC
    if sip_client.is_registered and sip_client.is_running:
        try:
            call_info = await sip_client.make_outbound_call(target, tenant_id=tenant_id, caller_id=tenant_did)
            from_num = call_info.get("from") or tenant_did
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
            tenant_did = get_tenant_did(tenant_id, vendor="exotel") or settings.EXOTEL_CALLER_ID
            call_info = trigger_exotel_outbound_call(target, tenant_id=tenant_id, caller_id=tenant_did)
            from_num = call_info.get("from") or tenant_did
            return {
                "status": "success",
                "message": f"Calling {target} via Exotel Gateway ({from_num})...",
                "call_sid": call_info.get("call_sid"),
                "vendor": "exotel",
                "data": call_info
            }
        except HTTPException:
            raise
        except Exception as e:
            print(f"[Outbound] Exotel dispatch error: {e}")
            raise HTTPException(status_code=500, detail="Failed to dispatch Exotel call.")

    raise HTTPException(
        status_code=400,
        detail=f"No active telephony gateway available. Asterisk client is not registered ({settings.RPDIGITAL_ASTERISK_HOST}) and Exotel credentials are not configured."
    )

@router.get("/recent")
@router.get("/active-calls")
async def get_active_calls(
    tenant_id: Optional[str] = Query(None, description="Optional tenant ID to filter call logs"),
    auth: Dict[str, Any] = Depends(get_current_tenant_auth),
    db: AsyncSession = Depends(get_db)
):
    """Returns the most recent calls, strictly scoped to a tenant the caller may access (admins may list all)."""
    requested = tenant_id.strip() if tenant_id and tenant_id.strip() else None
    is_admin = bool(auth.get("is_admin"))
    target_tid = resolve_tenant_scope(auth, requested) if (requested or not is_admin) else None
    try:
        if target_tid:
            result = await db.execute(text("""
                SELECT call_id, caller_number, start_time, end_time, duration, outcome_tag, transferred,
                       summary, primary_concern, caller_sentiment, action_required, executive_notes
                FROM calls 
                WHERE tenant_id IN :tids
                ORDER BY start_time DESC 
                LIMIT 25
            """).bindparams(bindparam("tids", expanding=True)), {"tids": tenant_alias_ids(target_tid)})
        elif is_admin:
            result = await db.execute(text("""
                SELECT call_id, caller_number, start_time, end_time, duration, outcome_tag, transferred,
                       summary, primary_concern, caller_sentiment, action_required, executive_notes
                FROM calls 
                ORDER BY start_time DESC 
                LIMIT 25
            """))
        else:
            return {"calls": []}

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
        print(f"[Outbound] Call log query error: {e}")
        return {"calls": [], "error": "Failed to load call logs."}

@router.get("/{call_id}/transcript")
async def get_call_transcript(
    call_id: str, 
    auth: Dict[str, Any] = Depends(get_current_tenant_auth),
    db: AsyncSession = Depends(get_db)
):
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
        if not call_row:
            raise HTTPException(status_code=404, detail="Call record not found.")

        call_tid = call_row[2]
        if not verify_tenant_access(call_tid, auth):
            # 404 rather than 403 so call IDs of other tenants can't be confirmed
            raise HTTPException(status_code=404, detail="Call record not found.")

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
    except HTTPException:
        raise
    except Exception as e:
        print(f"[Outbound] Transcript load error: {e}")
        raise HTTPException(status_code=500, detail="Failed to load transcript.")

