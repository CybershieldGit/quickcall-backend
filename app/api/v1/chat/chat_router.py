from app.core.auth import get_optional_tenant_auth, verify_tenant_access
"""
Chat Agent API Router — Enterprise Multi-Tenant Conversational Text & Web Chat
Supports:
- REST message turns with multi-turn history & context
- Streaming responses via Server-Sent Events (SSE)
- RAG semantic search across tenant-specific documents
- Intent detection: human agent escalation, guardrails, off-topic detection
- Multi-tier LLM fallback: Groq -> Gemini Flash Lite -> RAM engine
- Embeddable web chat widget configuration
"""
import uuid
import time
import json
import logging
from typing import Optional, List, Dict, Any, AsyncGenerator

from fastapi import APIRouter, HTTPException, Query, Request, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.config import settings
from app.core.auth import tenant_canonical_alias
from app.core.rate_limit import rate_limit_request, enforce_rate_limit
import re

# Public chat session IDs are bearer secrets: only server-issued random IDs are accepted from clients
_PUBLIC_CHAT_SESSION_RE = re.compile(r"^chat_[0-9a-f]{12,32}$")
_MAX_CHAT_MESSAGE = 2000
from app.core.tenant_service import (
    load_tenant_config, normalize_tenant_id, get_current_escalation_number
)
from app.core.intent_detector import (
    detect_escalation_intent, is_off_topic_or_troll_query, get_tenant_off_topic_reply,
    is_identity_or_bot_query, detect_brochure_intent
)
from app.core.messaging_service import (
    extract_phone_number, extract_email_address, send_brochure, get_tenant_brochure_url
)
from app.core.llm_service import call_llm, call_llm_stream
from app.core import rag_engine
from app.core.session_store import save_session, get_session

logger = logging.getLogger("qwickcall.chat")
router = APIRouter(prefix="/api/v1/chat", tags=["Chat Agent"])


# -----------------------------------------------------------------------------
# Request & Response Models
# -----------------------------------------------------------------------------
class ChatMessageRequest(BaseModel):
    tenant_id: str = "idigitalstudies"
    agent_id: Optional[str] = None
    phone_number: Optional[str] = None
    session_id: Optional[str] = None
    message: Optional[str] = None
    query: Optional[str] = None
    text: Optional[str] = None
    user_speech: Optional[str] = None
    language: Optional[str] = None  # e.g., "hi-IN", "en-IN"
    channel: Optional[str] = "web_chat"  # "web_chat", "whatsapp", "portal"
    stream: bool = False


class ChatStartRequest(BaseModel):
    tenant_id: str = "idigitalstudies"
    agent_id: Optional[str] = None
    phone_number: Optional[str] = None
    language: Optional[str] = "hi-IN"
    customer_name: Optional[str] = None
    channel: Optional[str] = "web_chat"


class ChatClearRequest(BaseModel):
    session_id: str


class BrochureDispatchRequest(BaseModel):
    tenant_id: str = "idigitalstudies"
    session_id: Optional[str] = None
    channel: str = "whatsapp"  # "whatsapp" or "email"
    destination: str  # 10-digit phone number or email address
    customer_name: Optional[str] = "Student"


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------
def _get_business_info(tenant_id: str, config: Optional[Dict] = None) -> tuple:
    """Extracts (business_name, advisor_name) dynamically from tenant config."""
    cfg = config or load_tenant_config(tenant_id) or {}
    b_name = cfg.get("business_name")
    tid = normalize_tenant_id(tenant_id)

    if not b_name:
        if tid == "idigitalstudies" or "digital" in tid:
            b_name = "iDigitalStudies"
        elif tid == "phonotel" or "phono" in tid:
            b_name = "Phono Tel"
        else:
            b_name = "Customer Support"

    advisor = (
        cfg.get("advisor_name")
        or cfg.get("agent_name")
        or cfg.get("business_rules", {}).get("agent_name")
        or cfg.get("business_rules", {}).get("advisor_name")
    )
    if not advisor:
        persona = cfg.get("persona", "")
        import re
        m = re.search(r"You are (?:named |called )?['\"]?([A-Z][a-z]+)['\"]?", persona)
        if m and m.group(1).lower() not in ("a", "an", "the", "helpful", "friendly", "professional"):
            advisor = m.group(1)
        elif "phono" in tid:
            advisor = "Rohan"
        else:
            advisor = "Aanya"

    return b_name, advisor


def _get_greeting(tenant_id: str, config: Optional[Dict], lang: str = "hi-IN") -> str:
    """Generates polite chat greeting customized per tenant."""
    cfg = config or load_tenant_config(tenant_id) or {}
    b_name, advisor = _get_business_info(tenant_id, cfg)
    custom_greeting = cfg.get("greeting") or cfg.get("business_rules", {}).get("greeting")
    if custom_greeting:
        return custom_greeting.replace("{agent_name}", advisor).replace("{business_name}", b_name)

    if lang.startswith("en"):
        return f"Hello! Welcome to {b_name}. I am {advisor}, your digital advisor. How can I assist you today?"
    return f"Namaste! {b_name} mein aapka swagat hai. Main {advisor} hoon. Main aapki kya madad kar sakta hoon?"


def _get_or_create_chat_session(
    session_id: Optional[str],
    tenant_id: str,
    lang: Optional[str] = None,
    agent_id: Optional[str] = None,
    trusted: bool = False
) -> Dict[str, Any]:
    """
    Retrieves existing session or provisions a new one.
    Untrusted (client-supplied) IDs must look like server-issued chat_ IDs, so web visitors
    can't attach to WhatsApp (wa_<phone>) or voice-call sessions.
    """
    if session_id and not trusted and not _PUBLIC_CHAT_SESSION_RE.match(session_id):
        session_id = None
    sid = session_id or f"chat_{uuid.uuid4().hex}"
    sess = get_session(sid)

    # If tenant_id or agent_id changed, discard old cached session to prevent cross-tenant conversation bleed
    if sess and (sess.get("tenant_id") != tenant_id or (agent_id and sess.get("agent_id") != agent_id)):
        sess = None

    if not sess:
        # If agent_id not explicitly provided, try to find the tenant's active assigned agent
        if not agent_id:
            try:
                with sync_engine.connect() as conn:
                    row = conn.execute(
                        text("""
                            SELECT assigned_agent_id FROM tenant_phone_numbers 
                            WHERE (tenant_id = :tid OR tenant_id = :canon) AND assigned_agent_id IS NOT NULL 
                            ORDER BY id ASC LIMIT 1
                        """),
                        {"tid": tenant_id, "canon": tenant_canonical_alias(tenant_id)}
                    ).fetchone()
                    if row and row[0]:
                        agent_id = row[0]
            except Exception:
                pass

        cfg = load_tenant_config(tenant_id, agent_id=agent_id) or {
            "persona": "You are a helpful and polite customer advisor.",
            "business_name": "Customer Support",
            "business_rules": {},
            "escalation_number": settings.DEFAULT_ESCALATION_NUMBER,
            "guardrail_topics": []
        }
        chosen_lang = lang or "hi-IN"
        greeting = _get_greeting(tenant_id, cfg, chosen_lang)
        sess = {
            "session_id": sid,
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "language": chosen_lang,
            "state": "MAIN_LOOP",
            "turn_index": 1,
            "config": cfg,
            "history": [
                {"role": "assistant", "content": greeting}
            ],
            "escalate": False,
            "created_at": time.time()
        }
        save_session(sid, sess, ttl=7200)
    return sess


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------
@router.post("/start")
async def start_chat(req: ChatStartRequest, request: Request):
    """
    Initializes a new chat session for a tenant and returns the initial welcome greeting.
    """
    rate_limit_request(request, "chat_start", limit=20, window_seconds=600)
    try:
        tenant_id = normalize_tenant_id(req.tenant_id)
        config = load_tenant_config(tenant_id, agent_id=req.agent_id) or {}
        b_name, advisor = _get_business_info(tenant_id, config)
        lang = req.language or "hi-IN"
        session_id = f"chat_{uuid.uuid4().hex}"

        greeting = _get_greeting(tenant_id, config, lang)
        sess_data = {
            "session_id": session_id,
            "tenant_id": tenant_id,
            "agent_id": req.agent_id,
            "language": lang,
            "state": "MAIN_LOOP",
            "turn_index": 1,
            "config": config,
            "history": [
                {"role": "assistant", "content": greeting}
            ],
            "customer_name": req.customer_name,
            "escalate": False,
            "created_at": time.time()
        }
        save_session(session_id, sess_data, ttl=7200)

        return {
            "status": "success",
            "session_id": session_id,
            "tenant_id": tenant_id,
            "business_name": b_name,
            "agent_name": advisor,
            "language": lang,
            "greeting": greeting,
            "reply": greeting,
            "state": "GREETING"
        }
    except Exception as e:
        logger.error(f"[Chat API] Start chat error: {e}")
        raise HTTPException(status_code=500, detail="Failed to start chat session.")


@router.post("")
@router.post("/message")
async def send_chat_message(req: ChatMessageRequest, request: Request = None):
    """
    Processes a multi-turn chat message.
    Accepts: message, query, text, or user_speech.
    Returns: AI response, updated history, escalation flag, and metadata.
    """
    t0 = time.time()
    if request is not None:
        rate_limit_request(request, "chat_message", limit=40, window_seconds=300)
    try:
        tenant_id = normalize_tenant_id(req.tenant_id)
        raw_msg = req.message or req.query or req.text or req.user_speech or ""
        clean_msg = raw_msg.strip()[:_MAX_CHAT_MESSAGE]

        if not clean_msg:
            raise HTTPException(status_code=400, detail="Message text is required.")

        sess = _get_or_create_chat_session(req.session_id, tenant_id, req.language, agent_id=req.agent_id)
        session_id = sess["session_id"]
        config = sess.get("config") or load_tenant_config(tenant_id, agent_id=req.agent_id or sess.get("agent_id")) or {}
        b_name, advisor = _get_business_info(tenant_id, config)

        if req.language:
            sess["language"] = req.language

        curr_lang = sess.get("language", "hi-IN")
        esc_number = config.get("escalation_number") or get_current_escalation_number()

        # Append user turn to history
        sess["history"].append({"role": "user", "content": clean_msg})
        sess["turn_index"] = sess.get("turn_index", 1) + 1

        reply = ""
        escalate = False

        # 1. Guardrail / Bot Identity Query
        if is_identity_or_bot_query(clean_msg):
            role_desc = config.get("role_title") or "AI Agent"
            if curr_lang.startswith("en"):
                reply = f"I am {advisor}, {role_desc} at {b_name}. How can I assist you with our services today?"
            else:
                reply = f"Main {advisor} hoon, {b_name} se {role_desc}. Main aapki kya madad kar sakta hoon?"

        # 2. Human Escalation / Transfer Intent Detection
        elif detect_escalation_intent(clean_msg):
            escalate = True
            sess["escalate"] = True
            if curr_lang.startswith("en"):
                reply = (
                    f"I understand you would like to speak directly with our senior executive team. "
                    f"You can call or WhatsApp our senior counselor directly at {esc_number}, "
                    f"or leave your phone number here and our team will contact you promptly."
                )
            else:
                reply = (
                    f"Ji bilkul, main samajh sakti hoon. Hamare senior counselor se direct baat karne ke liye "
                    f"aap {esc_number} par call ya WhatsApp kar sakte hain. "
                    f"Aap apna number bhi yahan chhod sakte hain, hamari team aapse turant sampark karegi."
                )

        # 3. Brochure / Syllabus Request Detection
        elif detect_brochure_intent(clean_msg):
            ph = extract_phone_number(clean_msg)
            em = extract_email_address(clean_msg)
            rules = config.get("business_rules", {})
            b_url = get_tenant_brochure_url(tenant_id)

            if ph:
                import asyncio
                asyncio.create_task(
                    send_brochure(
                        tenant_id=tenant_id,
                        channel="whatsapp",
                        destination=ph,
                        customer_name=sess.get("customer_name") or "Student"
                    )
                )
                if curr_lang.startswith("en"):
                    reply = (
                        f"Great! I have dispatched the official {b_name} brochure to your WhatsApp at +91 {ph}. "
                        f"You can also download it right now here: {b_url}."
                    )
                else:
                    reply = (
                        f"Bahut badhiya! Maine {b_name} ka official brochure aapke WhatsApp number (+91 {ph}) par bhej diya hai. "
                        f"Aap isse yahan direct download bhi kar sakte hain: {b_url}."
                    )
            elif em:
                import asyncio
                asyncio.create_task(
                    send_brochure(
                        tenant_id=tenant_id,
                        channel="email",
                        destination=em,
                        customer_name=sess.get("customer_name") or "Student"
                    )
                )
                if curr_lang.startswith("en"):
                    reply = (
                        f"Perfect! I have emailed the official course brochure to {em}. "
                        f"You can also download it right now here: {b_url}."
                    )
                else:
                    reply = (
                        f"Maine brochure aapke email ({em}) par bhej diya hai. "
                        f"Aap isse yahan direct download bhi kar sakte hain: {b_url}."
                    )
            else:
                if curr_lang.startswith("en"):
                    reply = (
                        f"I'd be glad to share our brochure! Would you like me to send it to your WhatsApp or Email? "
                        f"Please share your 10-digit WhatsApp number or email address, or click to download directly: {b_url}."
                    )
                else:
                    reply = (
                        f"Main aapko brochure zaroor bhej sakti hoon! Kripya apna 10-digit WhatsApp number ya email address yahan share karein, "
                        f"ya yahan se direct download karein: {b_url}."
                    )

        # 4. Off-Topic / Trolling Guardrail Check
        else:
            off_topic = is_off_topic_or_troll_query(clean_msg)
            if off_topic:
                cat, _ = off_topic
                off_res = get_tenant_off_topic_reply(tenant_id, config=config, session_lang=curr_lang, category=cat)
                reply = off_res[0] if isinstance(off_res, (tuple, list)) else str(off_res)
            else:
                # 4. Multi-tier LLM Generation with RAG grounding
                try:
                    reply = call_llm(sess, clean_msg)
                except Exception as el:
                    logger.warning(f"[Chat API] LLM generation warning: {el}")
                    reply = f"Ji, main {b_name} se bol rahi hoon. Kripya apna prashna dobara poochein."

        # Append assistant reply to session history
        sess["history"].append({"role": "assistant", "content": reply})
        save_session(session_id, sess, ttl=7200)

        latency_ms = round((time.time() - t0) * 1000)

        return {
            "status": "success",
            "session_id": session_id,
            "tenant_id": tenant_id,
            "reply": reply,
            "response": reply,
            "agent_response": reply,
            "message": reply,
            "escalate": escalate,
            "escalation_contact": esc_number if escalate else None,
            "state": "ESCALATION" if escalate else "MAIN_LOOP",
            "turn_index": sess["turn_index"],
            "latency_ms": latency_ms
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Chat API] Message processing error: {e}")
        raise HTTPException(status_code=500, detail="Error generating chat response.")


@router.post("/stream")
async def stream_chat_message(req: ChatMessageRequest, request: Request):
    """
    Streaming chat endpoint using Server-Sent Events (SSE).
    Yields sentence chunks in real-time as they are synthesized by the LLM.
    """
    rate_limit_request(request, "chat_message", limit=40, window_seconds=300)
    tenant_id = normalize_tenant_id(req.tenant_id)
    raw_msg = req.message or req.query or req.text or req.user_speech or ""
    clean_msg = raw_msg.strip()[:_MAX_CHAT_MESSAGE]

    if not clean_msg:
        raise HTTPException(status_code=400, detail="Message text is required.")

    sess = _get_or_create_chat_session(req.session_id, tenant_id, req.language)
    session_id = sess["session_id"]
    config = sess.get("config") or load_tenant_config(tenant_id) or {}
    b_name, advisor = _get_business_info(tenant_id, config)
    curr_lang = sess.get("language", "hi-IN")
    esc_number = config.get("escalation_number") or get_current_escalation_number()

    sess["history"].append({"role": "user", "content": clean_msg})
    sess["turn_index"] = sess.get("turn_index", 1) + 1

    async def event_generator() -> AsyncGenerator[str, None]:
        # Fast path 1: Identity query
        if is_identity_or_bot_query(clean_msg):
            reply = (
                f"I am {advisor}, an AI agent at {b_name}."
                if curr_lang.startswith("en")
                else f"Main {advisor} hoon, {b_name} ki AI agent."
            )
            sess["history"].append({"role": "assistant", "content": reply})
            save_session(session_id, sess, ttl=7200)
            yield f"data: {json.dumps({'chunk': reply, 'done': True, 'escalate': False})}\n\n"
            return

        # Fast path 2: Human Escalation
        if detect_escalation_intent(clean_msg):
            sess["escalate"] = True
            reply = (
                f"I will connect you with our senior representative. Please call or WhatsApp {esc_number}."
                if curr_lang.startswith("en")
                else f"Aap hamare senior counselor ko direct call ya WhatsApp kar sakte hain: {esc_number} par."
            )
            sess["history"].append({"role": "assistant", "content": reply})
            save_session(session_id, sess, ttl=7200)
            yield f"data: {json.dumps({'chunk': reply, 'done': True, 'escalate': True, 'escalation_contact': esc_number})}\n\n"
            return

        # Fast path 3: Brochure Request
        if detect_brochure_intent(clean_msg):
            ph = extract_phone_number(clean_msg)
            em = extract_email_address(clean_msg)
            rules = config.get("business_rules", {})
            b_url = get_tenant_brochure_url(tenant_id)

            if ph:
                import asyncio
                asyncio.create_task(
                    send_brochure(
                        tenant_id=tenant_id,
                        channel="whatsapp",
                        destination=ph,
                        customer_name=sess.get("customer_name") or "Student"
                    )
                )
                reply = (
                    f"Great! I have sent the official {b_name} brochure to your WhatsApp at +91 {ph}. "
                    f"You can also download it right now here: {b_url}."
                    if curr_lang.startswith("en")
                    else f"Bahut badhiya! Maine {b_name} ka official brochure aapke WhatsApp number (+91 {ph}) par bhej diya hai. "
                    f"Aap isse yahan direct download bhi kar sakte hain: {b_url}."
                )
            elif em:
                import asyncio
                asyncio.create_task(
                    send_brochure(
                        tenant_id=tenant_id,
                        channel="email",
                        destination=em,
                        customer_name=sess.get("customer_name") or "Student"
                    )
                )
                reply = (
                    f"Perfect! I have emailed the official course brochure to {em}. "
                    f"You can also download it right now here: {b_url}."
                    if curr_lang.startswith("en")
                    else f"Maine brochure aapke email ({em}) par bhej diya hai. "
                    f"Aap isse yahan direct download bhi kar sakte hain: {b_url}."
                )
            else:
                reply = (
                    f"I'd be glad to share our brochure! Would you like me to send it to your WhatsApp or Email? "
                    f"Please share your 10-digit WhatsApp number or email address, or click to download directly: {b_url}."
                    if curr_lang.startswith("en")
                    else f"Main aapko brochure zaroor bhej sakti hoon! Kripya apna 10-digit WhatsApp number ya email address yahan share karein, "
                    f"ya yahan se direct download karein: {b_url}."
                )

            sess["history"].append({"role": "assistant", "content": reply})
            save_session(session_id, sess, ttl=7200)
            yield f"data: {json.dumps({'chunk': reply, 'done': True, 'escalate': False, 'brochure_url': b_url})}\n\n"
            return

        # Fast path 4: Off-Topic Guardrail
        off_topic = is_off_topic_or_troll_query(clean_msg)
        if off_topic:
            cat, _ = off_topic
            off_res = get_tenant_off_topic_reply(tenant_id, config=config, session_lang=curr_lang, category=cat)
            reply = off_res[0] if isinstance(off_res, (tuple, list)) else str(off_res)
            sess["history"].append({"role": "assistant", "content": reply})
            save_session(session_id, sess, ttl=7200)
            yield f"data: {json.dumps({'chunk': reply, 'done': True, 'escalate': False})}\n\n"
            return

        # Streaming LLM tokens
        full_reply = ""
        try:
            async for chunk in call_llm_stream(sess, clean_msg):
                if chunk:
                    full_reply += (" " + chunk) if full_reply else chunk
                    yield f"data: {json.dumps({'chunk': chunk, 'done': False})}\n\n"
        except Exception as e:
            logger.warning(f"[Chat API] Stream iteration error: {e}")
            if not full_reply:
                full_reply = f"Namaste! Main {advisor} hoon {b_name} se."
                yield f"data: {json.dumps({'chunk': full_reply, 'done': False})}\n\n"

        sess["history"].append({"role": "assistant", "content": full_reply.strip()})
        save_session(session_id, sess, ttl=7200)
        yield f"data: {json.dumps({'chunk': '', 'done': True, 'session_id': session_id})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/history/{session_id}")
async def get_chat_history(
    session_id: str,
    auth: Optional[Dict[str, Any]] = Depends(get_optional_tenant_auth)
):
    """
    Returns the transcript history for a given chat session.
    Anonymous visitors may read only their own server-issued web chat session;
    any other session (e.g. WhatsApp) requires a tenant credential for its workspace.
    """
    sess = get_session(session_id)
    is_public_id = bool(_PUBLIC_CHAT_SESSION_RE.match(session_id or ""))
    sess_tid = (sess or {}).get("tenant_id")
    allowed = bool(sess) and (
        (auth and sess_tid and verify_tenant_access(sess_tid, auth))
        or (not auth and is_public_id)
    )
    if not allowed:
        raise HTTPException(status_code=404, detail="Chat session not found or expired.")
    return {
        "status": "success",
        "session_id": session_id,
        "tenant_id": sess.get("tenant_id"),
        "language": sess.get("language"),
        "history": sess.get("history", []),
        "turn_count": len(sess.get("history", [])),
        "escalate": sess.get("escalate", False)
    }


@router.post("/clear")
@router.delete("/history/{session_id}")
async def clear_chat_history(session_id: Optional[str] = None, req: Optional[ChatClearRequest] = None):
    """
    Resets conversation history for a web chat session.
    """
    sid = session_id or (req.session_id if req else None)
    if not sid:
        raise HTTPException(status_code=400, detail="session_id is required.")
    if not _PUBLIC_CHAT_SESSION_RE.match(sid):
        raise HTTPException(status_code=400, detail="Invalid session_id.")

    sess = get_session(sid)
    if sess:
        tenant_id = sess.get("tenant_id", "idigitalstudies")
        cfg = sess.get("config") or load_tenant_config(tenant_id) or {}
        greeting = _get_greeting(tenant_id, cfg, sess.get("language", "hi-IN"))
        sess["history"] = [{"role": "assistant", "content": greeting}]
        sess["turn_index"] = 1
        sess["escalate"] = False
        save_session(sid, sess, ttl=7200)
        return {"status": "success", "message": "Chat history cleared.", "session_id": sid, "greeting": greeting}

    return {"status": "success", "message": "Session not found or already clean."}


@router.get("/widget-config/{tenant_id}")
async def get_widget_config(tenant_id: str):
    """
    Returns public metadata to initialize an embeddable website chat bubble widget.
    Includes branding, agent identity, greeting, and escalation contacts.
    """
    tid = normalize_tenant_id(tenant_id)
    cfg = load_tenant_config(tid) or {}
    b_name, advisor = _get_business_info(tid, cfg)
    esc_number = cfg.get("escalation_number") or get_current_escalation_number()

    return {
        "tenant_id": tid,
        "business_name": b_name,
        "advisor_name": advisor,
        "greeting_hi": _get_greeting(tid, cfg, "hi-IN"),
        "greeting_en": _get_greeting(tid, cfg, "en-IN"),
        "escalation_phone": esc_number,
        "escalation_whatsapp": f"https://wa.me/{esc_number.replace('+', '').replace(' ', '')}" if esc_number else None,
        "theme": {
            "primary_color": "#06B6D4",
            "accent_color": "#6366F1",
            "bg_color": "#090A0F",
            "font_family": "Inter, sans-serif"
        }
    }


@router.post("/send-brochure")
async def send_brochure_api(
    req: BrochureDispatchRequest,
    request: Request,
    auth: Optional[Dict[str, Any]] = Depends(get_optional_tenant_auth)
):
    """
    Direct endpoint to dispatch brochure via WhatsApp or Email.
    Powers website chat buttons, interactive forms, and CRM automations.
    Rate-limited per visitor and per destination so it can't be used as a spam relay.
    Authenticated portal/CRM staff are exempted from visitor IP rate limits.
    """
    is_staff = bool(auth and (auth.get("is_admin") or auth.get("tenant_id") or auth.get("reseller_id")))
    if not is_staff:
        rate_limit_request(request, "send_brochure", limit=5, window_seconds=3600)
    tenant_id = normalize_tenant_id(req.tenant_id)
    ch = req.channel.lower().strip()

    if "what" in ch or ch == "wa":
        phone = extract_phone_number(req.destination)
        if not phone:
            raise HTTPException(status_code=400, detail="Please provide a valid 10-digit mobile number for WhatsApp.")
        dest = phone
        clean_ch = "whatsapp"
    elif "mail" in ch or ch == "email":
        email = extract_email_address(req.destination)
        if not email:
            raise HTTPException(status_code=400, detail="Please provide a valid email address.")
        dest = email
        clean_ch = "email"
    else:
        raise HTTPException(status_code=400, detail="Channel must be either 'whatsapp' or 'email'.")

    enforce_rate_limit(f"send_brochure_dest:{dest.lower()}", limit=3, window_seconds=86400)
    if req.session_id and not _PUBLIC_CHAT_SESSION_RE.match(req.session_id):
        req.session_id = None

    result = await send_brochure(
        tenant_id=tenant_id,
        channel=clean_ch,
        destination=dest,
        customer_name=req.customer_name or "Student"
    )

    if req.session_id:
        sess = get_session(req.session_id)
        if sess:
            channel_label = "WhatsApp" if clean_ch == "whatsapp" else "Email"
            note = f"Official brochure dispatched via {channel_label} to {dest}."
            sess.setdefault("history", []).append({"role": "assistant", "content": note})
            save_session(req.session_id, sess, ttl=7200)

    return {
        "status": "success",
        "message": f"Brochure successfully dispatched via {clean_ch} to {dest}.",
        "channel": clean_ch,
        "destination": dest,
        "details": result
    }


@router.get("/brochure-info")
async def get_brochure_info(tenant_id: str = "idigitalstudies"):
    """
    Returns brochure metadata and download link for widgets and frontend embeds.
    """
    tid = normalize_tenant_id(tenant_id)
    cfg = load_tenant_config(tid) or {}
    rules = cfg.get("business_rules", {})
    b_name = cfg.get("business_name") or ("iDigitalStudies" if "digital" in tid else "Customer Support")
    brochure_url = get_tenant_brochure_url(tid)
    brochure_title = rules.get("brochure_title") or f"{b_name} Official Information & Brochure"

    return {
        "status": "success",
        "tenant_id": tid,
        "business_name": b_name,
        "brochure_title": brochure_title,
        "brochure_url": brochure_url,
        "channels_supported": ["whatsapp", "email"]
    }
