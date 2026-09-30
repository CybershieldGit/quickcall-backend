"""
Enterprise Voice Agent — Multi-Tenant State Machine
State Flow: GREETING -> MAIN_LOOP -> ESCALATION_ATTEMPT -> LEAD_CAPTURE -> COMPLETED
"""
import uuid
import json
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
from groq import Groq
from sqlalchemy import text
from app.config import settings
from app.db.session import sync_engine
from app.core import rag_engine

# In-memory session store: call_id -> session dict
SESSIONS: Dict[str, Dict[str, Any]] = {}

GUARDRAIL_KEYWORDS = [
    # Explicit escalation & human transfer requests
    "escalat", "transfer", "executive", "senior", "human", "person",
    "talk to someone", "speak to someone", "connect me", "connect to", "call someone",
    "representative", "counselor", "advisor", "supervisor", "manager",
    "operator", "cant answer", "can't answer", "real person", "actual person",
    "someone else", "another person", "put me through", "speak with someone",
    "talk to a person", "live agent", "support agent", "helpdesk",
    # Policy guardrail triggers
    "refund", "money back", "cancel enrollment", "fee waiver", "discount me",
    "placement guarantee", "100% job", "assured job", "guaranteed salary",
    "legal action", "consumer forum", "complaint", "sue"
]

def _load_tenant_config(tenant_id: str) -> Optional[Dict[str, Any]]:
    """Load tenant persona, business rules, escalation number from DB."""
    with sync_engine.connect() as conn:
        row = conn.execute(
            text("SELECT persona, business_rules, escalation_number, guardrail_topics FROM tenant_config WHERE tenant_id = :tid AND is_active = true LIMIT 1"),
            {"tid": tenant_id}
        ).fetchone()
    if not row:
        return None
    
    # Priority: settings.DEFAULT_ESCALATION_NUMBER (.env) > DB escalation_number
    esc_num = (settings.DEFAULT_ESCALATION_NUMBER or row[2] or "+917488851979").replace(" ", "").replace("-", "").strip()
    return {
        "persona": row[0],
        "business_rules": row[1] or {},
        "escalation_number": esc_num,
        "guardrail_topics": row[3] or []
    }

def _resolve_tenant_from_did(dialed_number: str) -> Optional[str]:
    """Resolve tenant_id from a dialed DID phone number."""
    with sync_engine.connect() as conn:
        row = conn.execute(
            text("SELECT tenant_id FROM tenant_phone_numbers WHERE number = :num AND is_active = true"),
            {"num": dialed_number}
        ).fetchone()
    return row[0] if row else None

def _log_call_start(call_id: str, tenant_id: str, caller_number: str):
    with sync_engine.connect() as conn:
        try:
            conn.execute(
                text("INSERT INTO calls (call_id, tenant_id, caller_number, start_time) VALUES (:cid, :tid, :caller, :now) ON CONFLICT (call_id) DO NOTHING"),
                {"cid": call_id, "tid": tenant_id, "caller": caller_number, "now": datetime.utcnow()}
            )
            conn.commit()
        except Exception as e:
            print(f"[Call Log Error] {e}")

def _log_call_end(call_id: str, outcome_tag: str, transferred: bool):
    with sync_engine.connect() as conn:
        conn.execute(
            text("UPDATE calls SET end_time = :now, outcome_tag = :tag, transferred = :tr WHERE call_id = :cid"),
            {"now": datetime.utcnow(), "tag": outcome_tag, "tr": transferred, "cid": call_id}
        )
        conn.commit()

def _log_transcript(call_id: str, tenant_id: str, turn_index: int, speaker: str, text_content: str):
    try:
        transcript_id = uuid.uuid4().hex
        with sync_engine.connect() as conn:
            conn.execute(
                text("INSERT INTO call_transcripts (transcript_id, call_id, tenant_id, turn_index, speaker, text) VALUES (:tid_pk, :cid, :tid, :ti, :sp, :txt)"),
                {"tid_pk": transcript_id, "cid": call_id, "tid": tenant_id, "ti": turn_index, "sp": speaker, "txt": text_content}
            )
            conn.commit()
    except Exception as e:
        print(f"[Transcript Log Error] {e}")

def _log_lead(tenant_id: str, call_id: str, name: str, phone: str, reason: str):
    try:
        lead_id = uuid.uuid4().hex
        with sync_engine.connect() as conn:
            conn.execute(
                text("INSERT INTO leads (lead_id, tenant_id, call_id, name, phone, reason) VALUES (:lid, :tid, :cid, :name, :phone, :reason)"),
                {"lid": lead_id, "tid": tenant_id, "cid": call_id, "name": name, "phone": phone, "reason": reason}
            )
            conn.commit()
    except Exception as e:
        print(f"[Lead Log Error] {e}")

def _detect_escalation_intent(user_text: str) -> Optional[Tuple[str, str]]:
    """
    Returns (reason, spoken_response) ONLY if:
    1. Caller explicitly requests to connect to executive/counselor/human/senior.
    2. Caller asks about fees / pricing / payment / EMI.
    3. Caller asks about job placement / guarantee / salary.
    4. Caller raises a refund or formal dispute.
    Otherwise returns None (agent answers normally without escalating).
    """
    t = user_text.lower()

    # 1. Explicit request to connect to executive / senior / human
    human_triggers = [
        "executive", "escalat", "transfer", "senior", "human", "person",
        "talk to someone", "speak to someone", "connect me", "connect to", "put me through",
        "representative", "supervisor", "manager", "real person", "actual person",
        "live agent", "counselor", "advisor", "cant answer", "can't answer"
    ]
    if any(k in t for k in human_triggers):
        return ("executive_requested", "Certainly! I am transferring your call to our senior admissions counselor right away. Please hold for just a moment.")

    # 2. Inquiries about fees, cost, pricing
    fee_triggers = [
        "fee", "fees", "cost", "pricing", "how much", "charges", "payment plan", "emi", "discount"
    ]
    if any(k in t for k in fee_triggers):
        return ("fee_inquiry", "For complete details on our course fees and available zero-cost EMI plans, let me connect you with our senior admissions counselor right away. Please hold.")

    # 3. Inquiries about placement, job guarantee, salary
    placement_triggers = [
        "placement", "job guarantee", "guaranteed job", "100% job", "salary", "package", "hiring", "job assistance"
    ]
    if any(k in t for k in placement_triggers):
        return ("placement_inquiry", "For details on our 100 percent placement assistance and hiring partners, I am transferring your call to our senior counselor right now. Please hold.")

    # 4. Refund / Grievance disputes
    policy_triggers = [
        "refund", "money back", "cancel enrollment", "complaint", "legal action", "consumer forum", "sue"
    ]
    if any(k in t for k in policy_triggers):
        return ("policy_dispute", "I will connect you with our senior counselor immediately to assist you with this. Please hold.")

    return None

def _check_repeated_confusion(history: List, current_q: str, threshold: int = 2) -> bool:
    """Returns True if the same or very similar user query appeared multiple times."""
    user_msgs = [h["content"].lower() for h in history if h["role"] == "user"]
    count = sum(1 for m in user_msgs if m[:40] == current_q.lower()[:40])
    return count >= threshold

def _call_llm(session: Dict, user_query: str) -> str:
    """Generate ultra-fast, natural, conversational response via Groq."""
    if not settings.GROQ_API_KEY:
        return "I'm sorry, I couldn't process that right now. Please call back in a moment."
    
    tenant_id = session["tenant_id"]
    config = session["config"]
    
    # Fast in-memory RAG lookup (<2ms)
    kb_chunks = rag_engine.search_knowledge(tenant_id=tenant_id, query=user_query, top_k=3)
    knowledge_context = "\n\n".join([f"[{c['source_doc']}]: {c['chunk_text']}" for c in kb_chunks]) if kb_chunks else "General digital marketing information."
    
    system_prompt = (
        f"{config['persona']}\n\n"
        f"FACTS & COURSE DETAILS:\n{knowledge_context}\n\n"
        "CONVERSATIONAL PHONE RULES:\n"
        "- You are speaking on a live phone call. Be warm, enthusiastic, and helpful.\n"
        "- Reply in 1 or 2 concise, spoken sentences (maximum 35 words). Never give long monologues.\n"
        "- Answer the caller's questions directly based on the facts provided above.\n"
        "- Do NOT offer to transfer or connect the caller to an advisor on your own. Always answer the question yourself.\n"
        "- Absolutely NO markdown, bullets, asterisks, or special characters. Speak natural conversational English."
    )
    
    messages = [{"role": "system", "content": system_prompt}]
    # Last 4 turns of history
    for turn in session["history"][-4:]:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": user_query})
    
    try:
        client = Groq(api_key=settings.GROQ_API_KEY)
        response = client.chat.completions.create(
            model="qwen/qwen3.8-27b",
            messages=messages,
            max_tokens=70,
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[LLM Primary Error] {e}")
        try:
            # Fast fallback to groq/compound-mini
            response = client.chat.completions.create(
                model="groq/compound-mini",
                messages=messages,
                max_tokens=65,
                temperature=0.3,
            )
            return response.choices[0].message.content.strip()
        except Exception as e2:
            print(f"[LLM Fallback Error] {e2}")
            return "That is wonderful! We offer a 3 to 4 month program with live projects and an internship. Would you like to know more about the syllabus or fees?"




class VoiceAgent:
    def __init__(self, call_id: str, tenant_id: str = "idigitalstudies", caller_number: str = "unknown"):
        self.call_id = call_id
        self.tenant_id = tenant_id
        
        if call_id not in SESSIONS:
            config = _load_tenant_config(tenant_id) or {
                "persona": "You are a helpful IDS admissions advisor.",
                "business_rules": {},
                "escalation_number": settings.DEFAULT_ESCALATION_NUMBER,
                "guardrail_topics": []
            }
            SESSIONS[call_id] = {
                "state": "GREETING",
                "tenant_id": tenant_id,
                "caller_number": caller_number,
                "config": config,
                "history": [],
                "turn_index": 0,
                "escalation_number": config.get("escalation_number", settings.DEFAULT_ESCALATION_NUMBER),
                # Lead capture state
                "lead_name": None,
                "lead_phone": caller_number,
                "lead_reason": None,
                "lead_step": "name",  # name -> phone -> reason -> done
            }
            _log_call_start(call_id, tenant_id, caller_number)
        
        self.session = SESSIONS[call_id]
        self.config = self.session["config"]

    def get_greeting(self) -> str:
        name = self.config.get("business_rules", {}).get("agent_name", "Aanya")
        return (
            f"Hello! Welcome to iDigitalStudies. I'm Aanya, your AI admissions advisor. "
            f"How can I help you today? You can ask me about our Digital Marketing program, fees, batch timings, or internship details."
        )

    def process_turn(self, user_speech: str) -> Dict[str, Any]:
        session = self.session
        state = session["state"]
        user_clean = user_speech.strip()
        turn_idx = session["turn_index"]

        # Log caller turn to DB
        _log_transcript(self.call_id, self.tenant_id, turn_idx, "caller", user_clean)
        session["turn_index"] += 1
        session["history"].append({"role": "user", "content": user_clean})

        # 1. Check intent: ONLY escalate if caller asks for executive/human, fees, or placement
        escalation = _detect_escalation_intent(user_clean)
        repeated_confusion = _check_repeated_confusion(session["history"], user_clean)

        if escalation or repeated_confusion:
            reason, spoken_msg = escalation if escalation else (
                "repeated_confusion",
                "Let me connect you with our senior admissions counselor right away. Please hold for just a moment."
            )
            print(f"[VoiceAgent] Escalation triggered: {reason} | Caller speech: '{user_clean}'")
            session["state"] = "ESCALATION_ATTEMPT"
            self._log_agent(spoken_msg)
            return {
                "response": spoken_msg,
                "is_final": False,
                "state": "ESCALATION_ATTEMPT",
                "escalation_number": session["escalation_number"],
                "escalate": True
            }

        if state in ("GREETING", "MAIN_LOOP"):
            session["state"] = "MAIN_LOOP"
            return self._main_loop_response(user_clean)

        elif state == "ESCALATION_ATTEMPT":
            session["state"] = "LEAD_CAPTURE"
            session["lead_step"] = "name"
            agent_text = "I'm sorry, our counselor is currently on another line. May I have your name so we can call you right back?"
            self._log_agent(agent_text)
            return {"response": agent_text, "is_final": False, "state": "LEAD_CAPTURE"}

        elif state == "LEAD_CAPTURE":
            return self._handle_lead_capture(user_clean)

        else:  # COMPLETED
            return {"response": "Thank you for calling iDigitalStudies. Have a great day!", "is_final": True, "state": "COMPLETED"}

    def _main_loop_response(self, user_query: str) -> Dict[str, Any]:
        """Answer caller query directly from knowledge base. Never self-escalate."""
        agent_text = _call_llm(self.session, user_query)
        self._log_agent(agent_text)
        return {"response": agent_text, "is_final": False, "state": "MAIN_LOOP", "escalate": False}

    def _handle_lead_capture(self, user_speech: str) -> Dict[str, Any]:
        session = self.session
        step = session["lead_step"]

        if step == "name":
            session["lead_name"] = user_speech
            session["lead_step"] = "phone"
            agent_text = f"Thank you {user_speech}! And could you please share your best contact number?"
            self._log_agent(agent_text)
            return {"response": agent_text, "is_final": False, "state": "LEAD_CAPTURE"}

        elif step == "phone":
            session["lead_phone"] = user_speech
            session["lead_step"] = "reason"
            agent_text = "Got it! And briefly, what were you looking to know — fees, batches, or something else?"
            self._log_agent(agent_text)
            return {"response": agent_text, "is_final": False, "state": "LEAD_CAPTURE"}

        elif step == "reason":
            session["lead_reason"] = user_speech
            session["state"] = "COMPLETED"
            session["lead_step"] = "done"
            
            # Persist lead to DB
            _log_lead(
                tenant_id=self.tenant_id,
                call_id=self.call_id,
                name=session["lead_name"] or "Unknown",
                phone=session["lead_phone"] or "Unknown",
                reason=user_speech
            )
            _log_call_end(self.call_id, "escalation_failed_lead_captured", False)
            
            agent_text = (
                f"Perfect! I've noted your details, {session.get('lead_name', 'there')}. "
                f"Our counselor will call you back very soon. Thank you for your interest in iDigitalStudies and have a wonderful day!"
            )
            self._log_agent(agent_text)
            return {"response": agent_text, "is_final": True, "state": "COMPLETED"}

        return {"response": "Thank you. Goodbye!", "is_final": True, "state": "COMPLETED"}

    def mark_transferred(self):
        """Call this when live Twilio transfer succeeds."""
        self.session["state"] = "COMPLETED"
        _log_call_end(self.call_id, "transferred", True)

    def mark_escalation_failed(self):
        """Call this when Twilio transfer times out — triggers lead capture."""
        self.session["state"] = "ESCALATION_ATTEMPT"

    def _log_agent(self, text_content: str):
        """Log agent response to DB and history."""
        idx = self.session["turn_index"]
        _log_transcript(self.call_id, self.tenant_id, idx, "agent", text_content)
        self.session["turn_index"] += 1
        self.session["history"].append({"role": "assistant", "content": text_content})
