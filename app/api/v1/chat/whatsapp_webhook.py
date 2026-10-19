"""
Enterprise WhatsApp Webhook Router — Meta Cloud API & Twilio Inbound Messaging
Handles:
- Meta Webhook Verification (GET hub.mode, hub.challenge, hub.verify_token)
- Inbound WhatsApp Chat Messages (POST from Meta Cloud API or Twilio)
- Autonomous RAG Grounding & Conversational AI Turn Resolution
- Immediate Outbound Reply via configured WhatsApp Provider (Meta / Twilio / Mock)
"""
import time
import json
import hmac
import base64
import hashlib
import logging
from typing import Optional, Dict, Any

from fastapi import APIRouter, Request, Response, HTTPException, Query, BackgroundTasks, Depends
from app.config import settings
from app.core.auth import get_current_admin
from app.core.tenant_service import normalize_tenant_id, load_tenant_config
from app.services.messaging.brochure_service import send_whatsapp_message, get_tenant_brochure_url
from app.api.v1.chat.chat_router import _get_or_create_chat_session, _get_business_info
from app.core.llm_service import call_llm
from app.core.intent_detector import (
    detect_escalation_intent, is_off_topic_or_troll_query, get_tenant_off_topic_reply,
    is_identity_or_bot_query, detect_brochure_intent
)
from app.core.session_store import save_session

logger = logging.getLogger("qwickcall.whatsapp_webhook")
router = APIRouter(prefix="/api/v1/whatsapp", tags=["WhatsApp Webhook"])


@router.get("/webhook")
async def verify_meta_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token")
):
    """
    Meta WhatsApp Cloud API Webhook Verification Handshake.
    When you configure your webhook in Meta Developer Portal:
    URL: https://<your-domain>/api/v1/whatsapp/webhook
    Verify Token: matches WHATSAPP_VERIFY_TOKEN in .env
    """
    expected_token = settings.WHATSAPP_VERIFY_TOKEN
    if (
        expected_token
        and hub_mode == "subscribe"
        and hmac.compare_digest(hub_verify_token or "", expected_token)
    ):
        logger.info("✅ [Meta WhatsApp Webhook] Verification successful")
        return Response(content=str(hub_challenge), media_type="text/plain")

    logger.warning(f"❌ [Meta WhatsApp Webhook] Verification failed (mode: '{hub_mode}', token configured: {bool(expected_token)})")
    raise HTTPException(status_code=403, detail="Webhook verification token mismatch")


def _verify_meta_signature(raw_body: bytes, signature_header: Optional[str]) -> bool:
    """Validates Meta's X-Hub-Signature-256 (HMAC-SHA256 of the raw body with the app secret)."""
    secret = settings.WHATSAPP_APP_SECRET
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature_header[len("sha256="):], expected)


def _verify_twilio_signature(url: str, params: Dict[str, Any], signature_header: Optional[str]) -> bool:
    """Validates X-Twilio-Signature (base64 HMAC-SHA1 of URL + sorted POST params with the auth token)."""
    token = settings.TWILIO_AUTH_TOKEN
    if not token or not signature_header:
        return False
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    expected = base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(signature_header, expected)


def _signature_required() -> bool:
    # The mock provider is for local development only; real providers must sign their webhooks
    return (settings.WHATSAPP_PROVIDER or "mock").lower() != "mock" or settings.IS_PRODUCTION


async def _process_and_reply_whatsapp(
    from_number: str,
    user_text: str,
    tenant_id: str = "idigitalstudies"
):
    """
    Background worker that runs RAG LLM turn for WhatsApp message and sends reply.
    """
    try:
        clean_msg = (user_text or "").strip()
        if not clean_msg:
            return

        session_id = f"wa_{from_number.replace('+', '').replace(' ', '')}"
        sess = _get_or_create_chat_session(session_id, tenant_id, "hi-IN", trusted=True)
        config = sess.get("config") or load_tenant_config(tenant_id) or {}
        b_name, advisor = _get_business_info(tenant_id, config)
        curr_lang = sess.get("language", "hi-IN")

        # Detect English or Hindi
        if any(w in clean_msg.lower().split() for w in ["hi", "hello", "course", "fees", "duration", "details", "syllabus"]):
            curr_lang = "en-IN"
            sess["language"] = "en-IN"

        sess["history"].append({"role": "user", "content": clean_msg})
        sess["turn_index"] = sess.get("turn_index", 1) + 1

        reply = ""

        # 1. Identity Query
        if is_identity_or_bot_query(clean_msg):
            role_desc = config.get("role_title") or "Admissions Advisor"
            if curr_lang.startswith("en"):
                reply = f"Hello! I am {advisor}, {role_desc} at {b_name}. How can I assist you with our courses today?"
            else:
                reply = f"Namaste! Main {advisor} hoon, {b_name} se. Main aapki kya madad kar sakta hoon?"

        # 2. Escalation Intent
        elif detect_escalation_intent(clean_msg):
            esc_num = config.get("escalation_number") or getattr(settings, "DEFAULT_ESCALATION_NUMBER", "+919560732274")
            if curr_lang.startswith("en"):
                reply = (
                    f"I would be glad to connect you with our senior counselor. "
                    f"You can speak with them directly at {esc_num}, or our team will call you back on this number shortly."
                )
            else:
                reply = (
                    f"Ji zaroor, aap hamare senior counselor se direct baat kar sakte hain: {esc_num}. "
                    f"Hamari team bhi aapko jald hi is number par contact karegi."
                )

        # 3. Brochure Request
        elif detect_brochure_intent(clean_msg):
            rules = config.get("business_rules", {})
            b_url = get_tenant_brochure_url(tenant_id)
            b_title = rules.get("brochure_title") or f"{b_name} Official Brochure"
            reply = (
                f"Here is the official {b_name} brochure: {b_url}\n\n"
                f"Please let me know if you have any questions or require further details!"
            )
            # Send message with media attachment
            await send_whatsapp_message(
                to_number=from_number,
                message=reply,
                media_url=b_url,
                media_filename=f"{b_name}_Brochure.pdf",
                tenant_id=tenant_id
            )
            sess["history"].append({"role": "assistant", "content": reply})
            save_session(session_id, sess, ttl=86400)
            return

        # 4. Off-Topic Check
        elif is_off_topic_or_troll_query(clean_msg):
            cat, _ = is_off_topic_or_troll_query(clean_msg)
            off_res = get_tenant_off_topic_reply(tenant_id, config=config, session_lang=curr_lang, category=cat)
            reply = off_res[0] if isinstance(off_res, (tuple, list)) else str(off_res)

        # 5. Core RAG LLM Generation
        else:
            try:
                reply = call_llm(sess, clean_msg)
            except Exception as e:
                logger.error(f"[WhatsApp LLM Error] {e}")
                reply = f"Thank you for contacting {b_name}. How can I assist you with our training programs today?"

        sess["history"].append({"role": "assistant", "content": reply})
        save_session(session_id, sess, ttl=86400)

        # Send outbound WhatsApp reply
        await send_whatsapp_message(
            to_number=from_number,
            message=reply,
            tenant_id=tenant_id
        )
    except Exception as exc:
        logger.error(f"❌ [WhatsApp Background Reply Error] {exc}", exc_info=True)


@router.post("/webhook")
async def handle_whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks
):
    """
    Receives incoming WhatsApp messages from Meta Cloud API or Twilio WhatsApp.
    Dispatches immediate processing in background tasks and returns 200 OK.
    """
    content_type = request.headers.get("content-type", "")

    # 1. Meta WhatsApp Cloud API (JSON Payload)
    if "application/json" in content_type:
        raw_body = await request.body()
        if _signature_required() and not _verify_meta_signature(raw_body, request.headers.get("x-hub-signature-256")):
            logger.warning("❌ [Meta WhatsApp Webhook] Rejected request with missing/invalid signature")
            return Response(content="Invalid signature", status_code=403)
        try:
            body = json.loads(raw_body)
        except Exception:
            return Response(content="Invalid JSON", status_code=400)

        # Meta sends status receipts (sent, delivered, read) as well as messages
        entries = body.get("entry", [])
        for entry in entries:
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])
                for msg in messages:
                    from_num = msg.get("from")  # e.g., "919876543210"
                    msg_type = msg.get("type")

                    user_text = ""
                    if msg_type == "text":
                        user_text = msg.get("text", {}).get("body", "")
                    elif msg_type == "button":
                        user_text = msg.get("button", {}).get("text", "")
                    elif msg_type == "interactive":
                        interactive = msg.get("interactive", {})
                        if interactive.get("type") == "button_reply":
                            user_text = interactive.get("button_reply", {}).get("title", "")
                        elif interactive.get("type") == "list_reply":
                            user_text = interactive.get("list_reply", {}).get("title", "")

                    if from_num and user_text:
                        logger.info(f"📩 [Meta WhatsApp Inbound] From: +{from_num} | Message: {user_text}")
                        background_tasks.add_task(
                            _process_and_reply_whatsapp,
                            from_number=from_num,
                            user_text=user_text,
                            tenant_id="idigitalstudies"
                        )

        return {"status": "ok", "provider": "meta"}

    # 2. Twilio WhatsApp (Form-URLencoded Payload)
    elif "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        public_url = f"{settings.BASE_URL.rstrip('/')}{request.url.path}"
        if _signature_required() and not _verify_twilio_signature(public_url, dict(form), request.headers.get("x-twilio-signature")):
            logger.warning("❌ [Twilio WhatsApp Webhook] Rejected request with missing/invalid signature")
            return Response(content="Invalid signature", status_code=403)
        from_raw = form.get("From", "")  # e.g., "whatsapp:+919876543210"
        body_text = form.get("Body", "")

        from_num = from_raw.replace("whatsapp:", "").strip()
        if from_num and body_text:
            logger.info(f"📩 [Twilio WhatsApp Inbound] From: {from_num} | Message: {body_text}")
            background_tasks.add_task(
                _process_and_reply_whatsapp,
                from_number=from_num,
                user_text=body_text,
                tenant_id="idigitalstudies"
            )
        return Response(content="<Response></Response>", media_type="application/xml")

    return {"status": "ignored", "content_type": content_type}


@router.get("/status")
async def get_whatsapp_status(admin_user: Dict[str, Any] = Depends(get_current_admin)):
    """
    Returns the active WhatsApp provider configuration status.
    """
    provider = (settings.WHATSAPP_PROVIDER or "mock").lower()
    return {
        "status": "active",
        "provider": provider,
        "meta_configured": bool(settings.WHATSAPP_META_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID),
        "meta_phone_number_id": settings.WHATSAPP_PHONE_NUMBER_ID or None,
        "twilio_configured": bool(settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN),
        "twilio_from": settings.TWILIO_WHATSAPP_FROM if settings.TWILIO_ACCOUNT_SID else None,
        "webhook_url": f"{settings.BASE_URL}/api/v1/whatsapp/webhook",
        "verify_token_configured": bool(settings.WHATSAPP_VERIFY_TOKEN),
        "signature_verification": bool(settings.WHATSAPP_APP_SECRET or settings.TWILIO_AUTH_TOKEN)
    }
