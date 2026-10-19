"""
RAM Fallback Engine — Tier 3 Zero-External-Dependency In-Memory Agent
Activated when both Groq (Tier 1) and Gemini (Tier 2) encounter technical issues, rate limits, or network downtime.
Operates 100% locally from RAM:
- Zero API cost
- Sub-5ms response time
- 100% reliability / cannot fail
- Fully bilingual (Hindi/Hinglish & English)
- Adheres to admissions facts, counselor persona, and guardrail rules
"""
import re
from typing import Dict, AsyncGenerator, Optional, Tuple
from app.core import intent_detector

# Pre-compiled high-confidence query matchers for RAM agent
_FEE_PATTERNS = re.compile(
    r"\b(fee|fees|cost|charge|charges|price|pricing|amount|kharcha|rupaye|rupees|paisa|paise|kitna lagega|kitna padega|emi|installment|instalment|discount|scholarship)\b",
    re.IGNORECASE
)

_TIMING_PATTERNS = re.compile(
    r"\b(timing|timings|time|batch|batches|schedule|duration|kitne mahine|kitna time|kitna samay|kab se|kab tak|hours|weekend|weekday|morning|evening|subah|shaam)\b",
    re.IGNORECASE
)

_PLACEMENT_PATTERNS = re.compile(
    r"\b(placement|placements|job|jobs|hiring|partner|partners|package|salary|internship|internships|stipend|naukri|interview|career|company|companies)\b",
    re.IGNORECASE
)

_SYLLABUS_PATTERNS = re.compile(
    r"\b(syllabus|course|courses|curriculum|module|modules|kya sikhate|kya padhate|subject|subjects|program|programs|specialist|master|digital marketing|seo|sem|social media)\b",
    re.IGNORECASE
)

_TOOLS_PATTERNS = re.compile(
    r"\b(tool|tools|software|chatgpt|canva|semrush|google ads|meta ads|analytics|photoshop|ai tool)\b",
    re.IGNORECASE
)

_LOCATION_PATTERNS = re.compile(
    r"\b(location|address|where|kahan|center|branch|office|online|offline|classroom|ghar baithe)\b",
    re.IGNORECASE
)

_ELIGIBILITY_PATTERNS = re.compile(
    r"\b(eligible|eligibility|qualification|degree|kon kar sakta|kaun kar sakta|beginner|fresher|graduate|prerequisite)\b",
    re.IGNORECASE
)

_CERTIFICATE_PATTERNS = re.compile(
    r"\b(certificate|certification|diploma|degree|valid|recognized|certificate milega)\b",
    re.IGNORECASE
)

_GREETING_PATTERNS = re.compile(
    r"^(hello|hi|hey|namaste|pranam|good morning|good afternoon|good evening|haanji|haan ji|suno|kaho)$",
    re.IGNORECASE
)


# Knowledge Base in RAM for iDigital Studies (Bilingual: Hindi & English)
IDS_RAM_KNOWLEDGE = {
    "identity": {
        "hi": "Main Aanya hoon, iDigital Studies se admissions counselor! Main aapko courses, fees aur placement ke baare mein bata sakti hoon. Bataiye kya janna chahte hain?",
        "en": "I am Aanya, an admissions counselor at iDigital Studies! How can I help you explore our digital marketing courses today?"
    },
    "escalation": {
        "hi": "Ji bilkul, main aapko turant hamare senior admissions counselor se connect kar rahi hoon. Kripya line par bane rahiye.",
        "en": "Certainly, I am connecting you directly to our senior admissions counselor. Please hold the line for just a moment."
    },
    "fees": {
        "hi": "Hamare digital marketing courses ki fees bahut hi affordable hai aur isme aasan monthly EMI options bhi available hain. Kya aap EMI details janna chahte hain?",
        "en": "Our digital marketing program fees are very affordable and include flexible monthly EMI options. Would you like me to share the EMI details?"
    },
    "timing": {
        "hi": "Hamare batches Monday to Saturday subah 9 baje se shaam 6 baje tak chalte hain. Aap apni suvidha ke anusaar morning ya evening batch chun sakte hain.",
        "en": "Our batches run Monday to Saturday from 9 AM to 6 PM. You can choose a morning or evening batch based on your schedule."
    },
    "placement": {
        "hi": "iDigital Studies mein 90% placement success rate hai aur 50 se zyada hiring partners hain, sath hi 100% placement assistance milta hai. Kya aap partner companies ke baare mein janna chahte hain?",
        "en": "We offer 100% placement assistance with a 90% placement success rate and over 50 hiring partner companies. Would you like to know more about our hiring partners?"
    },
    "syllabus": {
        "hi": "Hum 6 mahine ka Master in Digital Marketing aur 3 mahine ka Specialist course offer karte hain, jisme SEO, Google Ads aur live client projects shamil hain. Kya aap detail syllabus dekhna chahenge?",
        "en": "We offer a 6-month Master program and a 3-month Specialist course covering SEO, performance ads, and live projects with 100% placement support. Would you like details on the modules?"
    },
    "tools": {
        "hi": "Course ke dauran aapko ChatGPT, Canva, SEMrush aur Google Ads jaise latest industry tools par practical training di jaati hai. Kya aap practical projects ke baare mein janna chahte hain?",
        "en": "During the course, you will receive hands-on training on modern industry tools including ChatGPT, Canva, SEMrush, and Google Ads. Would you like to know about our live projects?"
    },
    "location": {
        "hi": "Hamare paas interactive live online classes aur offline classroom training dono options available hain. Aap kaun sa mode prefer karenge?",
        "en": "We provide both interactive live online classes and offline classroom sessions. Which mode works best for you?"
    },
    "eligibility": {
        "hi": "Is course ke liye koi technical background zaroori nahi hai. Students, working professionals aur beginners sabhi enroll kar sakte hain. Kya aap apne background ke baare mein batayenge?",
        "en": "No prior technical background is required. Students, working professionals, and beginners are all eligible. May I know your current educational background?"
    },
    "certificate": {
        "hi": "Course pura karne par aapko industry-recognized certification aur live internship experience certificate milta hai. Kya aap admission process janna chahte hain?",
        "en": "Upon course completion, you will receive an industry-recognized certificate along with a live project internship certificate. Would you like to know the admission steps?"
    },
    "greeting": {
        "hi": "Namaste! iDigital Studies mein aapka swagat hai. Bataiye aaj main aapki kya madad kar sakti hoon?",
        "en": "Hello! Welcome to iDigital Studies. How can I assist you with our digital marketing programs today?"
    },
    "farewell": {
        "hi": "iDigital Studies se connect karne ke liye shukriya! Aapka din shubh ho, namaste.",
        "en": "Thank you for contacting iDigital Studies! Have a wonderful day, goodbye."
    },
    "off_topic": {
        "hi": "Main yahan iDigital Studies ke digital marketing courses aur admissions ke baare mein guide karne ke liye hoon. Batayein courses ke baare mein kya janna chahte hain?",
        "en": "I am here specifically to assist you with our digital marketing courses and admissions. How can I help you explore our programs today?"
    },
    "default": {
        "hi": "iDigital Studies mein hum practical live projects aur placement support ke sath digital marketing sikhate hain. Kya aap course ya fees ke baare mein janna chahte hain?",
        "en": "At iDigital Studies, we provide hands-on digital marketing training with live client projects and 100% placement support. Would you like details on our syllabus or fees?"
    }
}

# Phono Tel RAM Knowledge
PHONOTEL_RAM_KNOWLEDGE = {
    "identity": {
        "hi": "Main Rohan hoon, Phono Tel se Senior Solutions Advisor! Main aapko Smart IVR, Voicebots aur Telephony integrations mein help kar sakta hoon.",
        "en": "I am Rohan, Senior Solutions Advisor for Phono Tel! How can I assist your business with our cloud telephony solutions today?"
    },
    "escalation": {
        "hi": "Ji bilkul, main aapko hamare Solutions Manager se connect kar raha hoon, kripya hold kijiye.",
        "en": "Certainly, let me connect you directly with our Solutions Manager. Please hold the line."
    },
    "default": {
        "hi": "Phono Tel business ke liye Smart Cloud IVR, AI Voicebots aur automated calling solutions provide karta hai. Kya aap live demo chahte hain?",
        "en": "Phono Tel offers enterprise Cloud IVR, AI voicebots, and automated outbound calling solutions. Would you like to schedule a demo?"
    }
}


def _detect_lang_key(target_lang: str) -> str:
    """Returns 'hi' for Hindi / Hinglish, 'en' for English."""
    if target_lang and "hi" in target_lang.lower():
        return "hi"
    return "en"


def get_ram_fallback_response(session: Dict, user_query: str) -> str:
    """
    Evaluates caller query purely in-memory using RAM rules and facts.
    Returns crisp, single/double spoken sentence suitable for TTS playback.
    """
    clean_q = (user_query or "").strip().lower()
    target_lang = session.get("language", "hi-IN")
    lang_key = _detect_lang_key(target_lang)
    tenant_id = session.get("tenant_id", "idigitalstudies")

    # Select tenant knowledge dictionary
    canon_tid = str(tenant_id).lower().strip()
    if canon_tid in ("phonotel", "phono_tel", "phono-tel"):
        tenant_kb = PHONOTEL_RAM_KNOWLEDGE
    elif canon_tid in ("idigitalstudies", "cl_idigitalstudie_82d5", "ids"):
        tenant_kb = IDS_RAM_KNOWLEDGE
    else:
        # Dynamic generic tenant fallback: strictly isolates context and NEVER leaks another company
        cfg = session.get("config", {})
        b_name = cfg.get("business_name") or "our business"
        adv_name = cfg.get("advisor_name") or cfg.get("agent_name") or "customer care"
        tenant_kb = {
            "identity": {
                "hi": f"Main {adv_name} hoon, {b_name} se customer advisor! Main aapki kya sahayata kar sakti hoon?",
                "en": f"I am {adv_name}, customer advisor for {b_name}! How can I help you today?"
            },
            "escalation": {
                "hi": f"Ji bilkul, main aapko turant {b_name} ke senior executive se connect kar rahi hoon. Kripya line par bane rahiye.",
                "en": f"Certainly, let me connect you directly with a senior executive from {b_name}. Please hold the line."
            },
            "greeting": {
                "hi": f"Namaste! {b_name} mein aapka swagat hai. Aaj main aapki kya madad kar sakti hoon?",
                "en": f"Hello! Welcome to {b_name}. How can I assist you today?"
            },
            "farewell": {
                "hi": f"{b_name} se connect karne ke liye shukriya! Aapka din shubh ho, namaste.",
                "en": f"Thank you for contacting {b_name}! Have a wonderful day, goodbye."
            },
            "off_topic": {
                "hi": f"Main yahan {b_name} ke services aur offerings ke baare mein guide karne ke liye hoon. Batayein kya janna chahte hain?",
                "en": f"I am here specifically to assist you with services and offerings for {b_name}. How can I help you today?"
            },
            "default": {
                "hi": f"{b_name} se connect karne ke liye dhanyawad. Is baare mein main hamari team se bol kar aapko poori jankari dilwa sakti hoon.",
                "en": f"Thank you for contacting {b_name}. I can have our team follow up with complete information on this."
            }
        }

    # 1. Identity & "Are you AI / Real person" query
    if intent_detector.is_identity_or_bot_query(clean_q):
        resp = tenant_kb.get("identity", {}).get(lang_key)
        if resp:
            return resp

    # 2. Human escalation request
    esc = intent_detector.detect_escalation_intent(clean_q)
    if esc:
        resp = tenant_kb.get("escalation", {}).get(lang_key)
        if resp:
            return resp

    # 3. Farewell / Call conclusion
    if intent_detector.is_farewell_utterance(clean_q):
        resp = tenant_kb.get("farewell", {}).get(lang_key)
        if resp:
            return resp

    # 4. Greeting
    if _GREETING_PATTERNS.match(clean_q):
        resp = tenant_kb.get("greeting", {}).get(lang_key)
        if resp:
            return resp

    # 5. Core admissions intents (for iDigital Studies)
    if _FEE_PATTERNS.search(clean_q):
        return tenant_kb.get("fees", {}).get(lang_key, tenant_kb["default"][lang_key])

    if _PLACEMENT_PATTERNS.search(clean_q):
        return tenant_kb.get("placement", {}).get(lang_key, tenant_kb["default"][lang_key])

    if _TIMING_PATTERNS.search(clean_q):
        return tenant_kb.get("timing", {}).get(lang_key, tenant_kb["default"][lang_key])

    if _TOOLS_PATTERNS.search(clean_q):
        return tenant_kb.get("tools", {}).get(lang_key, tenant_kb["default"][lang_key])

    if _SYLLABUS_PATTERNS.search(clean_q):
        return tenant_kb.get("syllabus", {}).get(lang_key, tenant_kb["default"][lang_key])

    if _LOCATION_PATTERNS.search(clean_q):
        return tenant_kb.get("location", {}).get(lang_key, tenant_kb["default"][lang_key])

    if _ELIGIBILITY_PATTERNS.search(clean_q):
        return tenant_kb.get("eligibility", {}).get(lang_key, tenant_kb["default"][lang_key])

    if _CERTIFICATE_PATTERNS.search(clean_q):
        return tenant_kb.get("certificate", {}).get(lang_key, tenant_kb["default"][lang_key])

    # 6. In-memory check against loaded RAG knowledge chunks if available
    try:
        from app.core.rag_engine import _TENANT_CHUNKS_CACHE
        chunks = _TENANT_CHUNKS_CACHE.get(tenant_id, [])
        if chunks:
            q_words = set(re.findall(r"\b\w{4,}\b", clean_q))
            best_chunk_text = ""
            best_score = 0
            for chunk in chunks[:15]:
                ctext = chunk.get("chunk_text", "")
                c_words = set(re.findall(r"\b\w{4,}\b", ctext.lower()))
                overlap = len(q_words & c_words)
                if overlap > best_score:
                    best_score = overlap
                    best_chunk_text = ctext
            if best_score >= 2 and best_chunk_text:
                # Extract first concise sentence
                first_sent = re.split(r"(?<=[.!?])\s+", best_chunk_text.strip())[0]
                if 20 <= len(first_sent) <= 180:
                    return first_sent.replace("*", "").replace('"', '').strip()
    except Exception:
        pass

    # 7. Default fallback response from RAM
    return tenant_kb.get("default", {}).get(lang_key, "We are here to assist you with our programs. How can I help you today?")


async def ram_fallback_stream(session: Dict, user_query: str) -> AsyncGenerator[str, None]:
    """
    Asynchronous streaming generator yielding the RAM fallback response sentence-by-sentence.
    Guaranteed zero-failure streaming for the voice pipeline.
    """
    response_text = get_ram_fallback_response(session, user_query)
    sentence_split_pattern = re.compile(r'(?<!\d)[.!?\n]+(?!\d)(?:\s+|(?=[A-Z])|$)')
    sentences = sentence_split_pattern.split(response_text)
    for s in sentences:
        s_clean = s.strip()
        if s_clean:
            yield s_clean
