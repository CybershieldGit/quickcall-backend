"""
Enterprise Voice Agent — State Machine & Call Orchestrator
States: GREETING -> MAIN_LOOP -> ESCALATION_ATTEMPT -> LEAD_CAPTURE -> COMPLETED
"""
from typing import Dict, Any, Optional, Tuple, List

from app.config import settings
from app.services.tenant.tenant_service import (
    load_tenant_config, load_agent_config, resolve_tenant_from_did, resolve_did_route,
    get_current_escalation_number, get_tenant_escalation_target,
    normalize_tenant_id
)
from app.services.telemetry.call_logger import (
    log_call_start, log_call_end, log_transcript, log_lead
)
from app.services.voice_agent.intent_detector import (
    detect_escalation_intent, check_repeated_confusion, is_farewell_utterance, is_agent_farewell,
    GUARDRAIL_KEYWORDS, is_off_topic_or_troll_query, get_tenant_off_topic_reply,
    detect_brochure_intent, detect_confirmation_intent
)
from app.services.messaging.brochure_service import (
    extract_phone_number, extract_email_address, format_phone_for_speech, format_email_for_speech,
    send_brochure
)
from app.services.ai.llm_service import call_llm, call_llm_stream
from app.services.telemetry.summarizer import generate_executive_summary

from app.core.session_store import (
    DISTRIBUTED_SESSIONS, save_session, get_session, update_session
)

# Distributed session store proxy (persisted to Redis, compatible with SESSIONS dict)
SESSIONS: Dict[str, Dict[str, Any]] = DISTRIBUTED_SESSIONS

# Backward-compatible alias exports for routers
_resolve_tenant_from_did = resolve_tenant_from_did
_resolve_did_route = resolve_did_route
_log_call_end = log_call_end


def _get_business_info(tenant_id: str, config: Optional[Dict] = None) -> tuple:
    """Extracts (business_name, advisor_name) dynamically from tenant config."""
    cfg = config or load_tenant_config(tenant_id) or {}
    b_name = cfg.get("business_name")
    tid = normalize_tenant_id(tenant_id)

    if not b_name:
        if tid == "idigitalstudies":
            b_name = "iDigitalStudies"
        elif tid == "phonotel":
            b_name = "Phono Tel"
        else:
            b_name = "Customer Care"

    advisor = cfg.get("advisor_name") or cfg.get("agent_name") or cfg.get("business_rules", {}).get("agent_name") or cfg.get("business_rules", {}).get("advisor_name")
    if not advisor:
        persona = cfg.get("persona", "")
        import re
        m = re.search(r"You are (?:named |called )?['\"]?([A-Z][a-z]+)['\"]?", persona)
        if m and m.group(1).lower() not in ("a", "an", "the", "helpful", "friendly", "professional", "polite"):
            advisor = m.group(1)
        elif tid == "phonotel":
            advisor = "Rohan"
        elif tid == "idigitalstudies":
            advisor = "Aanya"
        else:
            advisor = "Aanya" if tid == "idigitalstudies" else "Rohan"

    return b_name, advisor


def get_tenant_greeting(tenant_id: str, config: Optional[Dict] = None) -> str:
    cfg = config or load_tenant_config(tenant_id) or {}
    custom_greeting = cfg.get("greeting") or cfg.get("business_rules", {}).get("greeting")
    if custom_greeting:
        return custom_greeting.strip()
    b_name, advisor = _get_business_info(tenant_id, cfg)
    tid = normalize_tenant_id(tenant_id)
    if tid in ("idigitalstudies", "cl_idigitalstudie_82d5") or tid.startswith("cl_idigital"):
        return "Hello, I am Aanya from iDigital Studies, how can I help you?"
    return f"Hello, I am {advisor} from {b_name}, how can I help you?"


LANGUAGE_CONFIRMATIONS = {
    "hi-IN": "Ji bilkul! Ab main Hindi mein baat karungi. Aap digital marketing course ke baare mein kya janna chahte hain?",
    "en-IN": "Sure, I will continue in English. How can I assist you with our courses today?"
}

INDIC_SCRIPTS = {
    "hi-IN": ["हिंदी", "हिन्दी"]
}

LATIN_PATTERNS = [
    ("hi-IN", [r"\bhindi\b", r"\bhinglish\b", r"hindi\s*me(?:in)?\s*(?:bolo|baat)", r"hindi\s*mai", r"speak.*hindi", r"talk.*hindi", r"hindi\s*please"]),
    ("en-IN", [r"\benglish\b", r"\bangrezi\b", r"\bangreji\b", r"speak.*english", r"talk.*english", r"in\s*english", r"only\s*english", r"english\s*please"])
]


def detect_language_switch_intent(user_text: str, tenant_id: str = "", config: Optional[Dict] = None) -> Optional[Tuple[str, str]]:
    """
    Bilingual intent detector for caller language selection between Hindi and English.
    Protects substantive syllabus questions from false switches.
    """
    import re
    clean = user_text.lower().strip()
    words = clean.split()
    if not words:
        return None

    # Protect questions about syllabus / course medium of instruction
    teaching_markers = {"taught", "teach", "sikhate", "padhate", "sikhaya", "sikhaoge", "curriculum", "syllabus", "available in"}
    if any(m in clean for m in teaching_markers) and any(w in clean for w in ["in english", "in hindi"]):
        return None

    # 1. Native Indic Script Check (Hindi)
    for lang, markers in INDIC_SCRIPTS.items():
        if any(m in clean for m in markers):
            return lang, LANGUAGE_CONFIRMATIONS[lang]

    # 2. Latin and Hinglish Patterns Check
    switch_indicators = [
        "speak", "talk", "switch", "continue", "prefer", "please", "bolo", "baat",
        "can you", "could you", "can we", "aati hai", "bol sakti", "bol sakte", "only", "want"
    ]
    has_indicator = any(si in clean for si in switch_indicators)
    is_short = len(words) <= 4

    for lang, regexes in LATIN_PATTERNS:
        for rgx in regexes:
            if re.search(rgx, clean):
                if is_short or has_indicator:
                    return lang, LANGUAGE_CONFIRMATIONS[lang]

    return None


def detect_direct_language_choice(user_text: str, tenant_id: str = "", config: Optional[Dict] = None) -> Optional[Tuple[str, str]]:
    """Backwards-compatible wrapper delegating to universal detector."""
    return detect_language_switch_intent(user_text, tenant_id=tenant_id, config=config)


def detect_explicit_language_switch(user_text: str) -> Optional[Tuple[str, str]]:
    """Backwards-compatible wrapper delegating to universal detector."""
    return detect_language_switch_intent(user_text)


def get_tenant_audible_reply(tenant_id: str, config: Optional[Dict] = None, session_lang: str = "hi-IN") -> str:
    lang = session_lang or "hi-IN"
    if lang.startswith("en"):
        return "Yes, I can hear you loud and clear! How can I assist you today?"
    return "Ji, aapki aawaz bilkul saaf aa rahi hai! Main aapki kya madad kar sakti hoon?"


def get_tenant_identity_reply(tenant_id: str, config: Optional[Dict] = None, session_lang: str = "hi-IN", session: Optional[Dict] = None) -> str:
    b_name, advisor = _get_business_info(tenant_id, config)
    lang = session_lang or "hi-IN"

    count = 0
    if session is not None:
        count = session.get("identity_query_count", 0)
        session["identity_query_count"] = count + 1

    if lang.startswith("en"):
        replies = [
            f"I am {advisor}, an admissions counselor at {b_name}! I can help you with all course details, fees, and placement support. How can I assist you today?",
            f"Haha, don't worry, you are speaking directly with {advisor} from the {b_name} admissions desk! My goal is to guide you on our syllabus and 100% placement support. Are you looking to upskill or searching for a job placement?",
            f"I completely understand your curiosity! At {b_name}, we focus on live client campaigns and have placed over 2000 students in top companies. Should I walk you through our 6-month Master program or our fast-track Specialist course?",
            f"I'm right here with you! Let's talk about your career goals so you get the best guidance today. Which area of digital marketing interests you the most — SEO, performance ads, or complete live projects?"
        ]
    else:
        replies = [
            f"Main {b_name} se {advisor} hoon, aapki admissions counselor! Main aapko humare digital marketing courses, fees aur placement ke baare mein bata sakti hoon. Bataiye aap kya janna chahte hain?",
            f"Arey bilkul chinta mat kijiye, aap {b_name} admissions desk ki {advisor} se hi baat kar rahe hain! Mera kaam aapko sahi digital marketing course aur 100% placement support guide karna hai. Kya aap job placement ke liye course dekh rahe hain?",
            f"Main bilkul samajh sakti hoon! {b_name} mein hum live client projects aur practical digital marketing sikhate hain, jahan 90% se zyada students successfully place hote hain. Kya main aapko hamare Master course ya batch timings ke baare mein bataun?",
            f"Main yahan aapke saath hi hoon! Chaliye aapke career goals ki baat karte hain taaki aapko best guidance mile. Aapko digital marketing mein kis cheez mein sabse zyada interest hai — SEO, performance ads ya complete projects?"
        ]

    idx = min(count, len(replies) - 1)
    return replies[idx]


def get_tenant_affirmative_reply(tenant_id: str, config: Optional[Dict] = None, session_lang: str = "hi-IN") -> str:
    lang = session_lang or "hi-IN"
    if lang.startswith("en"):
        return "Yes, I am here! What details would you like to explore about our digital marketing courses today?"
    return "Ji, main sun rahi hoon! Aap digital marketing courses ke baare mein kya janna chahte hain?"


def get_tenant_farewell_reply(tenant_id: str, config: Optional[Dict] = None, session_lang: str = "hi-IN") -> str:
    b_name, _ = _get_business_info(tenant_id, config)
    lang = session_lang or "hi-IN"
    if lang.startswith("en"):
        return f"Thank you for contacting {b_name}! Feel free to reach out anytime. Have a wonderful day ahead, goodbye!"
    return f"{b_name} se sampark karne ke liye dhanyawad! Have a wonderful day ahead, goodbye!"


def is_repeat_or_clarify_request(clean_text: str) -> bool:
    """Detects if caller is asking the agent to repeat what was just said."""
    t = clean_text.lower().strip()
    triggers = {
        "repeat", "repeat that", "say again", "say that again", "pardon", "come again",
        "what did you say", "could you repeat", "can you repeat", "please repeat",
        "kya bola", "kya kaha", "dobara bolo", "phir se bolo", "dobara batao",
        "phir se batao", "samajh nahi aaya", "samajh nahi aya", "aawaz cut gayi",
        "awaz cut gayi", "awaz nahi aayi", "aawaz nahi aayi", "repeat please",
        "phir se boliye", "dobara boliye", "kya bole", "what did you say"
    }
    return t in triggers or any(t.startswith(prefix) for prefix in ["can you repeat", "could you repeat", "please repeat", "dobara", "phir se"])


def is_inaudible_or_noisy_utterance(user_clean: str) -> bool:
    """Detects if caller audio is pure line noise, breathing, or inaudible fragments."""
    clean = user_clean.lower().strip()
    if not clean:
        return True
    noise_markers = {"[noise]", "[inaudible]", "(noise)", "(inaudible)", "[music]", "[cough]", "[laughter]"}
    if any(m in clean for m in noise_markers):
        return True
    words = clean.split()
    if len(words) == 1 and words[0] in {"uh", "um", "ah", "er", "mm", "mmm", "shh", "huh", "ohh"}:
        return True
    return False


def get_tenant_repeat_reply(tenant_id: str, config: Optional[Dict], session: Dict) -> str:
    """Repeats the last substantive message spoken by the AI advisor."""
    last_msg = ""
    for h in reversed(session.get("history", [])):
        if h.get("role") == "assistant" and h.get("content"):
            c = h["content"].strip()
            if not c.startswith("Maaf kijiye") and not c.startswith("Sorry") and not c.startswith("I'm sorry"):
                last_msg = c
                break
    curr_lang = session.get("language", "hi-IN")
    if curr_lang.startswith("en"):
        return f"Sure, let me repeat that: {last_msg}" if last_msg else "I am here! How can I assist you with our courses today?"
    return f"Ji bilkul, main dobara batati hoon: {last_msg}" if last_msg else "Ji main sun rahi hoon, aap digital marketing course ke baare mein kya janna chahte hain?"


def get_tenant_noise_reply(tenant_id: str, config: Optional[Dict], session: Dict) -> str:
    """Politely informs the caller about heavy background noise and asks them to move to a quiet place."""
    curr_lang = session.get("language", "hi-IN")
    if curr_lang.startswith("en"):
        return "I'm sorry, there is quite a bit of background noise on your end. Could you please move to a quieter place and repeat that?"
    return "Maaf kijiye, aapke peeche kaafi background noise aa raha hai. Kya aap kisi shaant jagah par jaakar dobara bol sakte hain?"




class VoiceAgent:
    def __init__(self, call_id: str, tenant_id: str = "idigitalstudies", caller_number: str = "unknown", agent_id: Optional[str] = None):
        self.call_id = call_id
        self.tenant_id = normalize_tenant_id(tenant_id)
        self.agent_id = agent_id

        existing_sess = get_session(call_id)
        if not existing_sess:
            config = load_tenant_config(tenant_id, agent_id=agent_id) or {
                "persona": "You are a helpful advisor.",
                "business_name": "Customer Care",
                "business_rules": {},
                "escalation_number": settings.DEFAULT_ESCALATION_NUMBER,
                "guardrail_topics": []
            }
            resolved_aid = agent_id or config.get("agent_id")
            greeting_msg = get_tenant_greeting(tenant_id, config)
            sess_data = {
                "state": "GREETING",
                "tenant_id": tenant_id,
                "agent_id": resolved_aid,
                "caller_number": caller_number,
                "config": config,
                "history": [{"role": "assistant", "content": greeting_msg}],
                "turn_index": 1,
                "escalation_number": config.get("escalation_number", settings.DEFAULT_ESCALATION_NUMBER),
                "escalation_attempted": False,
                "transfer_accepted": False,
                "lead_name": None,
                "lead_phone": caller_number,
                "lead_reason": None,
                "lead_step": "name",
            }
            save_session(call_id, sess_data)
            log_call_start(call_id, tenant_id, caller_number, agent_id=resolved_aid)
            log_transcript(call_id, tenant_id, 0, "agent", greeting_msg)

        self.session = get_session(call_id) or {}
        self.agent_id = self.session.get("agent_id") or agent_id
        self.config = self.session.get("config") or load_tenant_config(tenant_id, agent_id=self.agent_id) or {}

    def get_greeting(self) -> str:
        return get_tenant_greeting(self.tenant_id, self.config)

    def process_turn(self, user_speech: str) -> Dict[str, Any]:
        session = self.session
        state = session["state"]
        user_clean = user_speech.strip()
        turn_idx = session["turn_index"]

        log_transcript(self.call_id, self.tenant_id, turn_idx, "caller", user_clean)
        session["turn_index"] += 1
        session["history"].append({"role": "user", "content": user_clean})

        cleaned_words = "".join([c for c in user_clean.lower() if c.isalnum() or c.isspace()]).strip()

        # Language Selection or Mid-Call Switch (Active on every turn)
        switch_choice = detect_language_switch_intent(user_clean, self.tenant_id, self.config)
        if switch_choice:
            chosen_lang, switch_reply = switch_choice
            session["language"] = chosen_lang
            session["state"] = "MAIN_LOOP"
            save_session(self.call_id, session)
            self._log_agent(switch_reply)
            return {"response": switch_reply, "is_final": False, "state": "MAIN_LOOP", "escalate": False}

        curr_lang = session.get("language", "hi-IN")

        # Active brochure dispatch flow takes priority
        brochure_ctx = session.get("brochure_context")
        if brochure_ctx and brochure_ctx.get("active"):
            brochure_res = self._handle_brochure_turn(user_clean, curr_lang)
            if brochure_res:
                self._log_agent(brochure_res["response"])
                return brochure_res

        # Fast-paths: Audio check / caller identity check / simple affirmative
        if cleaned_words in {"am i audible", "am i audible now", "can you hear me", "are you able to hear me", "hello can you hear me"}:
            reply = get_tenant_audible_reply(self.tenant_id, self.config, curr_lang)
            self._log_agent(reply)
            return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}

        if cleaned_words in {"your name", "whats your name", "what is your name", "who is this", "who are you", "who am i talking to"}:
            reply = get_tenant_identity_reply(self.tenant_id, self.config, curr_lang)
            self._log_agent(reply)
            return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}

        if session["turn_index"] <= 3 and cleaned_words in {"yes", "yeah", "yep", "ha", "haan", "hello", "hi", "hey", "hello yes", "yes hello"}:
            reply = get_tenant_affirmative_reply(self.tenant_id, self.config, curr_lang)
            self._log_agent(reply)
            return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}

        # Check if caller asks to repeat / clarify
        if is_repeat_or_clarify_request(cleaned_words):
            reply = get_tenant_repeat_reply(self.tenant_id, self.config, session)
            self._log_agent(reply)
            return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}

        # Check if caller explicitly signals farewell
        if is_farewell_utterance(user_clean):
            farewell = get_tenant_farewell_reply(self.tenant_id, self.config, curr_lang)
            session["state"] = "COMPLETED"
            self._log_agent(farewell)
            log_call_end(self.call_id, "completed_graceful_exit", False)
            return {"response": farewell, "is_final": True, "state": "COMPLETED"}

        # Counter off-topic, trash, or trolling questions
        off_topic = is_off_topic_or_troll_query(user_clean)
        if off_topic:
            category, reason = off_topic
            strikes = session.get("off_topic_strikes", 0) + 1
            session["off_topic_strikes"] = strikes
            reply, should_disconnect = get_tenant_off_topic_reply(
                self.tenant_id, self.config, curr_lang, strike_count=strikes, category=category
            )
            self._log_agent(reply)
            if should_disconnect:
                session["state"] = "COMPLETED"
                save_session(self.call_id, session)
                log_call_end(self.call_id, "completed_troll_disconnect", False)
                return {"response": reply, "is_final": True, "state": "COMPLETED"}
            else:
                session["state"] = "MAIN_LOOP"
                save_session(self.call_id, session)
                return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}

        # Check new brochure dispatch request
        brochure_res = self._handle_brochure_turn(user_clean, curr_lang)
        if brochure_res:
            self._log_agent(brochure_res["response"])
            return brochure_res

        # Check escalation triggers
        escalation = None
        repeated_confusion = False
        if not session.get("escalation_attempted", False):
            escalation = detect_escalation_intent(user_clean)
            repeated_confusion = check_repeated_confusion(session["history"], user_clean)

        if escalation or repeated_confusion:
            esc_num, dept_name = get_tenant_escalation_target(self.tenant_id, user_clean)
            reason = escalation[0] if escalation else "repeated_confusion"
            spoken_msg = f"Let me connect you with our {dept_name} team right away. Please hold for just a moment."
            session["escalation_number"] = esc_num
            print(f"[VoiceAgent] Escalation triggered: {reason} | Dept: {dept_name} | Target: {esc_num} | Caller speech: '{user_clean}'")
            session["state"] = "ESCALATION_ATTEMPT"
            session["escalation_attempted"] = True
            self._log_agent(spoken_msg)
            return {
                "response": spoken_msg,
                "is_final": False,
                "state": "ESCALATION_ATTEMPT",
                "escalation_number": esc_num,
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
            b_name = self.config.get("business_name") or "our team"
            return {"response": f"Thank you for calling {b_name}. Have a great day!", "is_final": True, "state": "COMPLETED"}

    def _main_loop_response(self, user_query: str) -> Dict[str, Any]:
        """Answer caller query directly from knowledge base via LLM."""
        agent_text = call_llm(self.session, user_query)
        from app.core.tts_engine import _detect_sarvam_language
        detected_lang = _detect_sarvam_language(agent_text)
        if detected_lang:
            self.session["language"] = detected_lang
        self._log_agent(agent_text)
        is_farewell = is_agent_farewell(agent_text) or is_farewell_utterance(user_query)
        if is_farewell:
            self.session["state"] = "COMPLETED"
            save_session(self.call_id, self.session)
            log_call_end(self.call_id, "completed_graceful_exit", False)
            return {"response": agent_text, "is_final": True, "state": "COMPLETED", "escalate": False}
        return {"response": agent_text, "is_final": False, "state": "MAIN_LOOP", "escalate": False}

    async def stream_turn(self, user_speech: str, has_filler: bool = False):
        """
        Real-time asynchronous pipelined generator yielding (sentence_text, is_final, state, escalate, esc_num).
        Fast-paths and escalations yield immediately.
        Main loop queries yield each sentence the instant Groq produces it asynchronously.
        """
        session = self.session
        state = session["state"]
        if state == "COMPLETED":
            return
        user_clean = user_speech.strip()
        turn_idx = session["turn_index"]

        log_transcript(self.call_id, self.tenant_id, turn_idx, "caller", user_clean)
        session["turn_index"] += 1
        session["history"].append({"role": "user", "content": user_clean})
        save_session(self.call_id, session)

        cleaned_words = "".join([c for c in user_clean.lower() if c.isalnum() or c.isspace()]).strip()
        b_name = self.config.get("business_name") or "our team"

        # Language Selection or Mid-Call Switch (Active on every turn)
        switch_choice = detect_language_switch_intent(user_clean, self.tenant_id, self.config)
        if switch_choice:
            chosen_lang, switch_reply = switch_choice
            session["language"] = chosen_lang
            session["state"] = "MAIN_LOOP"
            save_session(self.call_id, session)
            self._log_agent(switch_reply)
            yield (switch_reply, False, "MAIN_LOOP", False, "")
            return

        curr_lang = session.get("language", "hi-IN")

        # Active brochure dispatch flow takes priority
        brochure_ctx = session.get("brochure_context")
        if brochure_ctx and brochure_ctx.get("active"):
            brochure_res = self._handle_brochure_turn(user_clean, curr_lang)
            if brochure_res:
                self._log_agent(brochure_res["response"])
                yield (brochure_res["response"], brochure_res.get("is_final", False), brochure_res.get("state", "MAIN_LOOP"), False, "")
                return

        # Fast-paths
        if cleaned_words in {"am i audible", "am i audible now", "can you hear me", "are you able to hear me", "hello can you hear me"}:
            reply = get_tenant_audible_reply(self.tenant_id, self.config, curr_lang)
            self._log_agent(reply)
            yield (reply, False, "MAIN_LOOP", False, "")
            return

        from app.core.intent_detector import is_identity_or_bot_query
        if is_identity_or_bot_query(user_clean) or cleaned_words in {"your name", "whats your name", "what is your name", "who is this", "who are you", "who am i talking to"}:
            reply = get_tenant_identity_reply(self.tenant_id, self.config, curr_lang, session=session)
            save_session(self.call_id, session)
            self._log_agent(reply)
            yield (reply, False, "MAIN_LOOP", False, "")
            return

        if session["turn_index"] <= 3 and cleaned_words in {"yes", "yeah", "yep", "ha", "haan", "hello", "hi", "hey", "hello yes", "yes hello"}:
            reply = get_tenant_affirmative_reply(self.tenant_id, self.config, curr_lang)
            self._log_agent(reply)
            yield (reply, False, "MAIN_LOOP", False, "")
            return

        # Check if caller asks to repeat / clarify
        if is_repeat_or_clarify_request(cleaned_words):
            reply = get_tenant_repeat_reply(self.tenant_id, self.config, session)
            self._log_agent(reply)
            yield (reply, False, "MAIN_LOOP", False, "")
            return

        if is_farewell_utterance(user_clean):
            farewell = get_tenant_farewell_reply(self.tenant_id, self.config, curr_lang)
            session["state"] = "COMPLETED"
            self._log_agent(farewell)
            log_call_end(self.call_id, "completed_graceful_exit", False)
            yield (farewell, True, "COMPLETED", False, "")
            return

        # Counter off-topic, trash, or trolling questions
        off_topic = is_off_topic_or_troll_query(user_clean)
        if off_topic:
            category, reason = off_topic
            strikes = session.get("off_topic_strikes", 0) + 1
            session["off_topic_strikes"] = strikes
            reply, should_disconnect = get_tenant_off_topic_reply(
                self.tenant_id, self.config, curr_lang, strike_count=strikes, category=category
            )
            self._log_agent(reply)
            if should_disconnect:
                session["state"] = "COMPLETED"
                save_session(self.call_id, session)
                log_call_end(self.call_id, "completed_troll_disconnect", False)
                yield (reply, True, "COMPLETED", False, "")
            else:
                session["state"] = "MAIN_LOOP"
                save_session(self.call_id, session)
                yield (reply, False, "MAIN_LOOP", False, "")
            return

        # Check new brochure dispatch request
        brochure_res = self._handle_brochure_turn(user_clean, curr_lang)
        if brochure_res:
            self._log_agent(brochure_res["response"])
            yield (brochure_res["response"], brochure_res.get("is_final", False), brochure_res.get("state", "MAIN_LOOP"), False, "")
            return

        # Escalations
        escalation = None
        repeated_confusion = False
        if not session.get("escalation_attempted", False):
            escalation = detect_escalation_intent(user_clean)
            repeated_confusion = check_repeated_confusion(session["history"], user_clean)

        if escalation or repeated_confusion:
            esc_num, dept_name = get_tenant_escalation_target(self.tenant_id, user_clean)
            if curr_lang.startswith("en"):
                spoken_msg = f"Certainly! Let me connect you directly with our {dept_name} counselor right away. Please stay on the line for just a moment."
            else:
                spoken_msg = f"Ji bilkul! Main aapki call hamare {dept_name} counselor ko connect kar rahi hoon. Kripya line par bane rahiye."
            session["escalation_number"] = esc_num
            session["state"] = "ESCALATION_ATTEMPT"
            session["escalation_attempted"] = True
            session["transfer_requested"] = True
            self._log_agent(spoken_msg)
            yield (spoken_msg, False, "ESCALATION_ATTEMPT", True, esc_num)
            return

        if state in ("GREETING", "MAIN_LOOP"):
            session["state"] = "MAIN_LOOP"
            save_session(self.call_id, session)

            from app.core.fillers import strip_duplicate_filler

            # 1. Semantic Cache Lookup (Redis Cloud RediSearch)
            from app.core.semantic_cache import search_semantic_cache, record_and_promote_query
            cached_res = await search_semantic_cache(self.tenant_id, user_clean)
            if cached_res and cached_res.get("sentences"):
                cached_sents = list(cached_res["sentences"])
                if has_filler and cached_sents:
                    cached_sents[0] = strip_duplicate_filler(cached_sents[0])
                is_farewell = is_farewell_utterance(user_clean) or any(is_agent_farewell(s) for s in cached_sents)
                for sent in cached_sents:
                    if sent.strip():
                        yield (sent.strip(), is_farewell, "COMPLETED" if is_farewell else "MAIN_LOOP", False, "")
                combined = " ".join([s for s in cached_sents if s.strip()]).strip()
                self._log_agent(combined)
                if is_farewell:
                    session["state"] = "COMPLETED"
                    save_session(self.call_id, session)
                    log_call_end(self.call_id, "completed_graceful_exit", False)
                return

            # 2. Cache Miss: Stream from Groq LLM
            sentences = []
            is_first_sentence = True
            try:
                async for sentence in call_llm_stream(session, user_clean):
                    if is_first_sentence and has_filler:
                        sentence = strip_duplicate_filler(sentence)
                        is_first_sentence = False
                    clean_s = sentence.strip()
                    if not clean_s:
                        continue
                    sentences.append(clean_s)
                    is_farewell = is_agent_farewell(clean_s) or is_farewell_utterance(user_clean)
                    if is_farewell:
                        session["state"] = "COMPLETED"
                        save_session(self.call_id, session)
                        log_call_end(self.call_id, "completed_graceful_exit", False)
                        self._log_agent(clean_s)
                        yield (clean_s, True, "COMPLETED", False, "")
                        break

                    # Safety fallback: If LLM output explicitly states it is connecting/transferring to a senior or executive
                    is_llm_transfer = not session.get("escalation_attempted", False) and any(
                        p in clean_s.lower() for p in [
                            "connect kar rahi hoon", "connect kar raha hoon", "transfer kar rahi hoon",
                            "transfer kar raha hoon", "connecting you to", "transferring you to",
                            "connect you directly", "put you through to", "line par bane rahiye"
                        ]
                    )
                    if is_llm_transfer:
                        esc_num, _ = get_tenant_escalation_target(self.tenant_id, user_clean)
                        session["escalation_number"] = esc_num
                        session["state"] = "ESCALATION_ATTEMPT"
                        session["escalation_attempted"] = True
                        session["transfer_requested"] = True
                        save_session(self.call_id, session)
                        self._log_agent(clean_s)
                        yield (clean_s, False, "ESCALATION_ATTEMPT", True, esc_num)
                        break
                    else:
                        yield (clean_s, False, "MAIN_LOOP", False, "")
            except Exception as e:
                print(f"[VoiceAgent LLM Stream Exception] {e}")

            # Fail-safe: If no sentences were produced (LLM down, rate limited, empty, or network error)
            if not sentences:
                from app.core.tts_engine import get_graceful_transfer_message
                esc_num, dept_name = get_tenant_escalation_target(self.tenant_id, user_clean)
                graceful_fallback = get_graceful_transfer_message(session.get("language", "hi-IN"))
                print(f"[VoiceAgent] 🛡️ Fail-safe activated: Zero LLM sentences produced -> Graceful escalation to {esc_num}")
                session["escalation_number"] = esc_num
                session["state"] = "ESCALATION_ATTEMPT"
                session["escalation_attempted"] = True
                session["transfer_requested"] = True
                save_session(self.call_id, session)
                self._log_agent(graceful_fallback)
                yield (graceful_fallback, False, "ESCALATION_ATTEMPT", True, esc_num)
                return

            if sentences:
                combined = " ".join(sentences).strip()
                from app.core.tts_engine import _detect_sarvam_language
                detected_lang = _detect_sarvam_language(combined)
                if detected_lang:
                    self.session["language"] = detected_lang
                    save_session(self.call_id, self.session)
                self._log_agent(combined)
                # Dynamic Popularity Caching: Tracks frequency and only promotes repeatedly asked queries
                import asyncio
                asyncio.create_task(record_and_promote_query(self.tenant_id, user_clean, combined, sentences))
            return

        elif state == "ESCALATION_ATTEMPT":
            session["state"] = "LEAD_CAPTURE"
            session["lead_step"] = "name"
            save_session(self.call_id, session)
            if curr_lang.startswith("en"):
                agent_text = "I apologize, our counselor is currently on another line. May I take your name so they can call you back right away?"
            else:
                agent_text = "Maaf kijiye, hamare counselor abhi doosri call par vyast hain. Kya main aapka naam jaan sakti hoon taaki hum aapko priority callback kar sakein?"
            self._log_agent(agent_text)
            yield (agent_text, False, "LEAD_CAPTURE", False, "")
            return

        elif state == "LEAD_CAPTURE":
            res = self._handle_lead_capture(user_clean)
            yield (res["response"], res.get("is_final", False), res.get("state", "LEAD_CAPTURE"), False, "")
            return

        else:
            yield (f"Thank you for calling {b_name}. Have a great day, goodbye!", True, "COMPLETED", False, "")
            return

    def _handle_brochure_turn(self, user_clean: str, curr_lang: str) -> Optional[Dict[str, Any]]:
        """
        Interactive multi-channel brochure dispatch via WhatsApp or Email:
        - Inquires channel preference (WhatsApp / Email) if not provided.
        - Captures phone number or email address.
        - Repeats phone number or email address back to caller to confirm.
        - Once confirmation is received, triggers dispatch via messaging_service and resumes conversation.
        """
        import re
        session = self.session
        brochure_ctx = session.get("brochure_context")
        is_en = curr_lang.startswith("en")

        # 1. Flow is not yet active: check if user triggered brochure intent
        if not brochure_ctx or not brochure_ctx.get("active"):
            # If brochure was already sent in this call, NEVER re-trigger unless caller explicitly asks to send it again
            if brochure_ctx and brochure_ctx.get("dispatched"):
                t_clean = re.sub(r'[^\w\s]', ' ', user_clean.lower())
                explicit_re_send = bool(re.search(r'\b(?:send\s+(?:again|another|one\s+more)|dobara\s+bhej|phir\s+se\s+bhej|email\s+(?:again|dobara)|whatsapp\s+(?:again|dobara))\b', t_clean))
                if not explicit_re_send:
                    return None
            else:
                last_agent = session["history"][-2]["content"] if len(session["history"]) >= 2 else ""
                intent = detect_brochure_intent(user_clean, last_agent_message=last_agent)
                if not intent:
                    return None

            channel = intent.get("channel")  # 'whatsapp', 'email', or None
            extracted_phone = extract_phone_number(user_clean)
            extracted_email = extract_email_address(user_clean)

            brochure_ctx = {
                "active": True,
                "channel": channel,
                "step": "init",
                "destination": None,
                "destination_speech": None
            }
            session["brochure_context"] = brochure_ctx

            # A. User provided email immediately
            if extracted_email:
                brochure_ctx["channel"] = "email"
                brochure_ctx["destination"] = extracted_email
                dest_speech = format_email_for_speech(extracted_email)
                brochure_ctx["destination_speech"] = dest_speech
                brochure_ctx["step"] = "confirm_destination"
                save_session(self.call_id, session)
                reply = (
                    f"I've noted your email address: {dest_speech}. Just to confirm, should I send the brochure here? Please say yes to confirm."
                    if is_en else
                    f"Maine aapka email: {dest_speech} note kar liya hai. Confirm karne ke liye, kya main brochure isi par bhej doon? Kripya confirm karne ke liye haan kahein."
                )
                return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}

            # B. User provided phone immediately
            if extracted_phone:
                brochure_ctx["channel"] = "whatsapp"
                brochure_ctx["destination"] = extracted_phone
                dest_speech = format_phone_for_speech(extracted_phone)
                brochure_ctx["destination_speech"] = dest_speech
                brochure_ctx["step"] = "confirm_destination"
                save_session(self.call_id, session)
                reply = (
                    f"I've noted your WhatsApp number: {dest_speech}. Just to confirm, should I send the brochure to this number? Please say yes to confirm."
                    if is_en else
                    f"Maine aapka WhatsApp number: {dest_speech} note kar liya hai. Confirm karne ke liye, kya main brochure isi number par bhej doon? Kripya confirm karne ke liye haan kahein."
                )
                return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}

            # C. Channel is Email, prompt for email address
            if channel == "email":
                brochure_ctx["step"] = "ask_email"
                session["play_beep"] = True
                save_session(self.call_id, session)
                reply = (
                    "Sure! Please say your email address after the beep, and I will note it down."
                    if is_en else
                    "Ji bilkul! Kripya beep ke baad apna email address batayein, main note kar loongi."
                )
                return {"response": reply, "is_final": False, "state": "MAIN_LOOP", "play_beep": True}

            # D. Channel is WhatsApp, check calling number or prompt for number
            if channel == "whatsapp":
                caller_num = session.get("caller_number") or ""
                clean_caller = "".join([c for c in caller_num if c.isdigit()])
                if len(clean_caller) >= 10:
                    brochure_ctx["step"] = "ask_whatsapp_number_or_same"
                    save_session(self.call_id, session)
                    sp_num = format_phone_for_speech(clean_caller[-10:])
                    reply = (
                        f"Sure! Should I send the brochure to your current calling number, {sp_num}, or a different WhatsApp number?"
                        if is_en else
                        f"Ji bilkul! Kya main brochure aapke is calling number {sp_num} par bhej doon, ya koi doosra WhatsApp number batayenge?"
                    )
                    return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}
                else:
                    brochure_ctx["step"] = "ask_whatsapp_number"
                    save_session(self.call_id, session)
                    reply = (
                        "Certainly! Could you please share your 10-digit WhatsApp number?"
                        if is_en else
                        "Ji bilkul! Kripya apna 10-digit WhatsApp number bataiye?"
                    )
                    return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}

            # E. Channel is not specified (e.g. user said "yes I want the brochure on my whats or mail")
            brochure_ctx["step"] = "ask_channel"
            save_session(self.call_id, session)
            reply = (
                "I'd be happy to send you the brochure! Would you like me to send it on WhatsApp or via Email?"
                if is_en else
                "Main aapko brochure zaroor bhej sakti hoon! Kya aap isse WhatsApp par chahte hain ya Email par?"
            )
            return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}

        # 2. Brochure flow is ALREADY ACTIVE
        step = brochure_ctx.get("step")

        # Check if caller wants to conclude the call (farewell)
        from app.core.intent_detector import is_farewell_utterance
        if is_farewell_utterance(user_clean):
            brochure_ctx["active"] = False
            session["brochure_context"] = brochure_ctx
            farewell = get_tenant_farewell_reply(self.tenant_id, self.config, curr_lang)
            session["state"] = "COMPLETED"
            save_session(self.call_id, session)
            log_call_end(self.call_id, "completed_graceful_exit", False)
            return {"response": farewell, "is_final": True, "state": "COMPLETED"}

        # Check if caller declines the brochure or wants to exit
        t_low = user_clean.lower()
        is_decline = bool(re.search(r'\b(?:no\s+no\s+enough|no\s+enough|no\s+thanks|not\s+now|not\s+right\s+now|cancel|stop|leave\s+it|don\'t\s+send|dont\s+send|nahi\s+chahiye|rehne\s+do|mat\s+bhejo|enough|kuch\s+nahi|no\s+need|not\s+needed|have\s+all\s+the\s+information|all\s+the\s+information|all\s+information|call\s+you\s+later|call\s+later|egg\s+count\s+liter|count\s+liter|later)\b', t_low))
        if step == "ask_channel" and bool(re.search(r'\b(?:no|nahi|nahin|na)\b', t_low)) and not (extract_phone_number(user_clean) or extract_email_address(user_clean)):
            is_decline = True

        if is_decline:
            brochure_ctx["active"] = False
            session["brochure_context"] = brochure_ctx
            save_session(self.call_id, session)

            # Check if caller accompanied the decline with a substantive query/question
            # e.g., "No, not right now. Just tell me what kind of AI tools you use and how will you teach me all those AI?"
            query_remainder = re.sub(
                r'^(?:no|not\s+now|not\s+right\s+now|no\s+thanks|no\s+need|leave\s+it|rehne\s+do|nahi|nahin|na|mat\s+bhejo|cancel|stop)[,\s.]*',
                '',
                t_low
            ).strip()

            if len(query_remainder) > 8:
                # Caller has an actual question! Return None so the main loop / LLM answers their question.
                return None

            reply = (
                "No problem at all! Feel free to ask anything else about our courses, or let me know how I can assist you."
                if is_en else
                "Koi baat nahi! Courses ya admissions ke baare mein aapka koi aur sawal ho to zaroor batayein."
            )
            return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}

        if step == "ask_channel":
            attempts = brochure_ctx.get("channel_attempts", 0) + 1
            brochure_ctx["channel_attempts"] = attempts

            # Phonetic aliases for WhatsApp and Email/Mail
            is_wa = bool(re.search(r'\b(?:whatsapp|whats\s+app|wa|phone|number|watsapp|what\'s\s+app|wats\s*app)\b', t_low))
            is_em = bool(re.search(r'\b(?:email|mail|gmail|neil|male|image|mel|mil|nail|mill|g\s*mail)\b', t_low))

            phone_num = extract_phone_number(user_clean)
            email_addr = extract_email_address(user_clean)

            if email_addr or (is_em and not is_wa):
                brochure_ctx["channel"] = "email"
                if email_addr:
                    dest_speech = format_email_for_speech(email_addr)
                    brochure_ctx["destination"] = email_addr
                    brochure_ctx["destination_speech"] = dest_speech
                    brochure_ctx["step"] = "confirm_destination"
                    save_session(self.call_id, session)
                    reply = (
                        f"I've noted {dest_speech}. Just to confirm, should I send the brochure to this email? Please say yes to confirm."
                        if is_en else
                        f"Maine {dest_speech} note kar liya hai. Confirm karne ke liye, kya main brochure isi email par bhej doon? Kripya haan kahein."
                    )
                    return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}
                else:
                    brochure_ctx["step"] = "ask_email"
                    session["play_beep"] = True
                    save_session(self.call_id, session)
                    reply = (
                        "Sure! Please say your email address after the beep, and I will note it down."
                        if is_en else
                        "Ji bilkul! Kripya beep ke baad apna email address batayein, main note kar loongi."
                    )
                    return {"response": reply, "is_final": False, "state": "MAIN_LOOP", "play_beep": True}

            elif phone_num or is_wa:
                brochure_ctx["channel"] = "whatsapp"
                if phone_num:
                    dest_speech = format_phone_for_speech(phone_num)
                    brochure_ctx["destination"] = phone_num
                    brochure_ctx["destination_speech"] = dest_speech
                    brochure_ctx["step"] = "confirm_destination"
                    save_session(self.call_id, session)
                    reply = (
                        f"I've noted {dest_speech}. Just to confirm, should I send the brochure to this WhatsApp number? Please say yes to confirm."
                        if is_en else
                        f"Maine {dest_speech} note kar liya hai. Confirm karne ke liye, kya main brochure isi WhatsApp number par bhej doon? Kripya haan kahein."
                    )
                    return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}
                else:
                    caller_num = session.get("caller_number") or ""
                    clean_caller = "".join([c for c in caller_num if c.isdigit()])
                    if len(clean_caller) >= 10:
                        brochure_ctx["step"] = "ask_whatsapp_number_or_same"
                        save_session(self.call_id, session)
                        sp_num = format_phone_for_speech(clean_caller[-10:])
                        reply = (
                            f"Should I send it to your current calling number, {sp_num}, or a different WhatsApp number?"
                            if is_en else
                            f"Kya main aapke is calling number {sp_num} par bhej doon, ya koi doosra number batayenge?"
                        )
                        return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}
                    else:
                        brochure_ctx["step"] = "ask_whatsapp_number"
                        save_session(self.call_id, session)
                        reply = (
                            "Please share your 10-digit WhatsApp number."
                            if is_en else
                            "Kripya apna 10-digit WhatsApp number bataiye."
                        )
                        return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}
            else:
                # If caller asks a question or retry limit reached (>= 2), exit brochure loop gracefully
                question_words = {"fee", "fees", "duration", "time", "timing", "batch", "batches", "placement", "salary", "location", "address", "offline", "online", "kya", "kitna", "kitni", "kab", "kahan", "course", "syllabus", "tools", "admission"}
                words_in_user = set(re.findall(r'\b[a-z0-9]+\b', t_low))
                if (words_in_user & question_words) or attempts >= 2:
                    brochure_ctx["active"] = False
                    session["brochure_context"] = brochure_ctx
                    save_session(self.call_id, session)
                    if words_in_user & question_words:
                        return None
                    reply = (
                        "No problem at all! Feel free to ask anything about our courses, fees, or placements, or let me know how I can help."
                        if is_en else
                        "Koi baat nahi! Courses, fees ya placement ke baare mein aapka koi aur sawal ho to zaroor batayein."
                    )
                    return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}

                reply = (
                    "Could you please tell me if you prefer WhatsApp or Email for receiving the brochure?"
                    if is_en else
                    "Kripya batayein, kya aap brochure WhatsApp par chahte hain ya Email par?"
                )
                return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}

        elif step == "ask_whatsapp_number_or_same":
            t_low = user_clean.lower()
            conf = detect_confirmation_intent(user_clean)
            is_same = (conf is True) or any(w in t_low for w in ["same", "current", "this", "isi", "isi pe", "isi number", "isi par", "isipar"])
            new_phone = extract_phone_number(user_clean)

            if is_same and not new_phone:
                caller_num = session.get("caller_number") or ""
                clean_caller = "".join([c for c in caller_num if c.isdigit()])[-10:]
                brochure_ctx["destination"] = clean_caller
                dest_speech = format_phone_for_speech(clean_caller)
                brochure_ctx["destination_speech"] = dest_speech
                brochure_ctx["step"] = "confirm_destination"
                save_session(self.call_id, session)
                reply = (
                    f"Got it! Confirming: sending brochure to {dest_speech} on WhatsApp. Is that correct?"
                    if is_en else
                    f"Samajh gayi! Confirm kar rahi hoon: brochure {dest_speech} par WhatsApp bhej doon. Kya yeh sahi hai?"
                )
                return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}
            elif new_phone:
                brochure_ctx["destination"] = new_phone
                dest_speech = format_phone_for_speech(new_phone)
                brochure_ctx["destination_speech"] = dest_speech
                brochure_ctx["step"] = "confirm_destination"
                save_session(self.call_id, session)
                reply = (
                    f"I've noted {dest_speech}. Just to confirm, should I send the brochure to this WhatsApp number? Please say yes to confirm."
                    if is_en else
                    f"Maine {dest_speech} note kar liya hai. Confirm karne ke liye, kya main brochure isi WhatsApp number par bhej doon? Kripya haan kahein."
                )
                return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}
            else:
                reply = (
                    "Could you please confirm if you want it on your calling number, or state your 10-digit WhatsApp number?"
                    if is_en else
                    "Kripya batayein kya is calling number par bhejein, ya apna 10-digit WhatsApp number bataiye?"
                )
                return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}

        elif step == "ask_whatsapp_number":
            # Check if user changed their mind to Email
            t_low = user_clean.lower()
            if any(w in t_low for w in ["email", "mail", "gmail"]):
                brochure_ctx["channel"] = "email"
                em = extract_email_address(user_clean)
                if em:
                    brochure_ctx["destination"] = em
                    dest_speech = format_email_for_speech(em)
                    brochure_ctx["destination_speech"] = dest_speech
                    brochure_ctx["step"] = "confirm_destination"
                    save_session(self.call_id, session)
                    reply = (
                        f"I've noted {dest_speech}. Just to confirm, should I send the brochure to this email address? Please say yes to confirm."
                        if is_en else
                        f"Maine {dest_speech} note kar liya hai. Confirm karne ke liye, kya main brochure isi email par bhej doon? Kripya haan kahein."
                    )
                    return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}
                else:
                    brochure_ctx["step"] = "ask_email"
                    save_session(self.call_id, session)
                    reply = (
                        "Sure! Could you please share your email address?"
                        if is_en else
                        "Ji bilkul! Kripya apna email address bataiye?"
                    )
                    return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}

            phone_num = extract_phone_number(user_clean)
            if phone_num:
                brochure_ctx["destination"] = phone_num
                dest_speech = format_phone_for_speech(phone_num)
                brochure_ctx["destination_speech"] = dest_speech
                brochure_ctx["step"] = "confirm_destination"
                save_session(self.call_id, session)
                reply = (
                    f"I've noted {dest_speech}. Just to confirm, should I send the brochure to this WhatsApp number? Please say yes to confirm."
                    if is_en else
                    f"Maine {dest_speech} note kar liya hai. Confirm karne ke liye, kya main brochure isi WhatsApp number par bhej doon? Kripya haan kahein."
                )
                return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}
            else:
                reply = (
                    "I didn't quite catch that. Could you please state your 10-digit WhatsApp number clearly?"
                    if is_en else
                    "Mujhe number theek se samajh nahi aaya. Kripya apna 10-digit WhatsApp number dobara bataiye?"
                )
                return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}

        elif step == "ask_email":
            # Check if user changed their mind to WhatsApp
            t_low = user_clean.lower()
            if any(w in t_low for w in ["whatsapp", "whats", "wa", "phone"]):
                brochure_ctx["channel"] = "whatsapp"
                ph = extract_phone_number(user_clean)
                if ph:
                    brochure_ctx["destination"] = ph
                    dest_speech = format_phone_for_speech(ph)
                    brochure_ctx["destination_speech"] = dest_speech
                    brochure_ctx["step"] = "confirm_destination"
                    save_session(self.call_id, session)
                    reply = (
                        f"I've noted {dest_speech}. Just to confirm, should I send the brochure to this WhatsApp number? Please say yes to confirm."
                        if is_en else
                        f"Maine {dest_speech} note kar liya hai. Confirm karne ke liye, kya main brochure isi WhatsApp number par bhej doon? Kripya haan kahein."
                    )
                    return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}
                else:
                    brochure_ctx["step"] = "ask_whatsapp_number"
                    save_session(self.call_id, session)
                    reply = (
                        "Sure! Could you please share your 10-digit WhatsApp number?"
                        if is_en else
                        "Ji bilkul! Kripya apna 10-digit WhatsApp number bataiye?"
                    )
                    return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}

            email_addr = extract_email_address(user_clean)
            if email_addr:
                brochure_ctx["destination"] = email_addr
                dest_speech = format_email_for_speech(email_addr)
                brochure_ctx["destination_speech"] = dest_speech
                brochure_ctx["step"] = "confirm_destination"
                save_session(self.call_id, session)
                reply = (
                    f"I've noted {dest_speech}. Just to confirm, should I send the brochure to this email address? Please say yes to confirm."
                    if is_en else
                    f"Maine {dest_speech} note kar liya hai. Confirm karne ke liye, kya main brochure isi email par bhej doon? Kripya haan kahein."
                )
                return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}
            else:
                session["play_beep"] = True
                save_session(self.call_id, session)
                reply = (
                    "Please say your email address clearly after the beep, for example name at gmail dot com."
                    if is_en else
                    "Kripya beep ke baad apna email address dobara batayein, jaise naam at gmail dot com."
                )
                return {"response": reply, "is_final": False, "state": "MAIN_LOOP", "play_beep": True}

        elif step == "confirm_destination":
            conf = detect_confirmation_intent(user_clean)
            dest = brochure_ctx.get("destination")
            channel = brochure_ctx.get("channel", "whatsapp")
            dest_speech = brochure_ctx.get("destination_speech") or dest

            if conf is True:
                # Dispatch brochure in background safely in both async and sync contexts
                import asyncio
                import threading
                def _dispatch_worker():
                    try:
                        asyncio.run(
                            send_brochure(
                                tenant_id=self.tenant_id,
                                channel=channel,
                                destination=dest,
                                customer_name=session.get("lead_name") or "Student"
                            )
                        )
                    except Exception as err:
                        print(f"[Brochure Dispatch Error] {err}")

                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(
                        send_brochure(
                            tenant_id=self.tenant_id,
                            channel=channel,
                            destination=dest,
                            customer_name=session.get("lead_name") or "Student"
                        )
                    )
                except RuntimeError:
                    threading.Thread(target=_dispatch_worker, daemon=True).start()

                # Reset brochure context and resume main loop
                session["brochure_context"] = {"active": False, "dispatched": True, "channel": channel, "destination": dest}
                save_session(self.call_id, session)

                ch_name = "WhatsApp" if channel == "whatsapp" else "Email"
                reply = (
                    f"Perfect! I have sent the brochure to your {ch_name}. You should receive it momentarily. Is there anything else about our courses I can assist you with?"
                    if is_en else
                    f"Bahut badhiya! Maine brochure aapke {ch_name} par bhej diya hai. Yeh aapko abhi mil jayega. Aur batayein, hamare courses ke baare mein aap aur kya janna chahte hain?"
                )
                return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}

            # Check if caller supplied new contact details in this turn (e.g. "No, my email is...")
            new_phone = extract_phone_number(user_clean)
            new_email = extract_email_address(user_clean)

            if new_email:
                brochure_ctx["channel"] = "email"
                brochure_ctx["destination"] = new_email
                dest_speech = format_email_for_speech(new_email)
                brochure_ctx["destination_speech"] = dest_speech
                save_session(self.call_id, session)
                reply = (
                    f"I've noted {dest_speech}. Just to confirm, should I send the brochure to this email? Please say yes to confirm."
                    if is_en else
                    f"Maine {dest_speech} note kar liya hai. Confirm karne ke liye, kya main brochure isi email par bhej doon? Kripya haan kahein."
                )
                return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}

            elif new_phone:
                brochure_ctx["channel"] = "whatsapp"
                brochure_ctx["destination"] = new_phone
                dest_speech = format_phone_for_speech(new_phone)
                brochure_ctx["destination_speech"] = dest_speech
                save_session(self.call_id, session)
                reply = (
                    f"I've noted {dest_speech}. Just to confirm, should I send the brochure to this WhatsApp number? Please say yes to confirm."
                    if is_en else
                    f"Maine {dest_speech} note kar liya hai. Confirm karne ke liye, kya main brochure isi number par bhej doon? Kripya haan kahein."
                )
                return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}

            elif conf is False:
                if channel == "email":
                    brochure_ctx["step"] = "ask_email"
                    session["play_beep"] = True
                    save_session(self.call_id, session)
                    reply = (
                        "No problem! Please say your correct email address after the beep, and I will note it down."
                        if is_en else
                        "Koi baat nahi! Kripya beep ke baad apna sahi email address batayein, main note kar loongi."
                    )
                    return {"response": reply, "is_final": False, "state": "MAIN_LOOP", "play_beep": True}
                else:
                    brochure_ctx["step"] = "ask_whatsapp_number"
                    session["play_beep"] = True
                    save_session(self.call_id, session)
                    reply = (
                        "No problem! Please state your correct 10-digit WhatsApp number after the beep."
                        if is_en else
                        "Koi baat nahi! Kripya beep ke baad apna sahi 10-digit WhatsApp number batayein."
                    )
                    return {"response": reply, "is_final": False, "state": "MAIN_LOOP", "play_beep": True}

            else:
                reply = (
                    f"Should I proceed with sending the brochure to {dest_speech}? Please say yes to confirm, or share the correct details."
                    if is_en else
                    f"Kya main {dest_speech} par brochure bhej doon? Kripya confirm karne ke liye haan kahein, ya sahi details batayein."
                )
                return {"response": reply, "is_final": False, "state": "MAIN_LOOP"}

        return None

    def _handle_lead_capture(self, user_speech: str) -> Dict[str, Any]:
        session = self.session
        step = session["lead_step"]
        b_name = self.config.get("business_name") or "our team"
        curr_lang = session.get("language", "hi-IN")
        is_en = curr_lang.startswith("en")

        if step == "name":
            session["lead_name"] = user_speech
            session["lead_step"] = "phone"
            save_session(self.call_id, session)
            agent_text = f"Thank you {user_speech}! And could you please share your best contact number?" if is_en else f"Dhanyawad {user_speech}! Aur kripya apna phone number share kar sakte hain?"
            self._log_agent(agent_text)
            return {"response": agent_text, "is_final": False, "state": "LEAD_CAPTURE"}

        elif step == "phone":
            session["lead_phone"] = user_speech
            session["lead_step"] = "reason"
            save_session(self.call_id, session)
            agent_text = "Got it! And briefly, what were you looking to know — fees, batches, or something else?" if is_en else "Samajh gayi! Aur aap mukhya roop se kis baare mein jankari chahte hain — fees, batch timings, ya placement?"
            self._log_agent(agent_text)
            return {"response": agent_text, "is_final": False, "state": "LEAD_CAPTURE"}

        elif step == "reason":
            session["lead_reason"] = user_speech
            session["state"] = "COMPLETED"
            session["lead_step"] = "done"
            save_session(self.call_id, session)

            log_lead(
                tenant_id=self.tenant_id,
                call_id=self.call_id,
                name=session["lead_name"] or "Unknown",
                phone=session["lead_phone"] or "Unknown",
                reason=user_speech
            )
            log_call_end(self.call_id, "escalation_failed_lead_captured", False)

            if is_en:
                agent_text = (
                    f"Perfect! I've noted your details, {session.get('lead_name', 'there')}. "
                    f"Our counselor will call you back very soon. Thank you for your interest in {b_name} and have a wonderful day!"
                )
            else:
                agent_text = (
                    f"Bahut badhiya! Maine aapki details note kar li hain, {session.get('lead_name', '')}. "
                    f"Hamare counselor aapko jald se jald call karenge. {b_name} se sampark karne ke liye dhanyawad aur aapka din shubh ho!"
                )
            self._log_agent(agent_text)
            return {"response": agent_text, "is_final": True, "state": "COMPLETED"}

        farewell_msg = f"Thank you. Goodbye!" if is_en else "Dhanyawad. Alvida!"
        return {"response": farewell_msg, "is_final": True, "state": "COMPLETED"}

    def mark_transferred(self):
        """Call this when live Exotel transfer succeeds."""
        self.session["state"] = "COMPLETED"
        self.session["transfer_accepted"] = True
        log_call_end(self.call_id, "transferred", True)

    def mark_escalation_failed(self):
        """Call this when Exotel transfer times out or is rejected by screening."""
        self.session["state"] = "MAIN_LOOP"
        self.session["escalation_attempted"] = True
        self.session["transfer_accepted"] = False
        caller_num = self.session.get("caller_number") or "unknown"
        log_lead(
            tenant_id=self.tenant_id,
            call_id=self.call_id,
            name="Caller (Priority Callback)",
            phone=caller_num,
            reason="Executive unavailable during call transfer — priority callback scheduled"
        )
        log_call_end(self.call_id, "escalation_failed_callback_scheduled", False)

    def _log_agent(self, text_content: str):
        """Log agent response to DB and history."""
        idx = self.session["turn_index"]
        log_transcript(self.call_id, self.tenant_id, idx, "agent", text_content)
        self.session["turn_index"] += 1
        self.session["history"].append({"role": "assistant", "content": text_content})
        save_session(self.call_id, self.session)

    def summarize(self, outcome: str = "completed") -> Dict[str, Any]:
        """Generate structured executive note & summary for this call."""
        return generate_executive_summary(
            call_id=self.call_id,
            tenant_id=self.tenant_id,
            history=self.session.get("history"),
            outcome=outcome
        )
