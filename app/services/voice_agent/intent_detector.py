"""
Intent & Guardrail Detection Module
Detects escalation requests, caller disputes, repeated confusion, and call sign-offs.
"""
import re
from typing import Optional, List, Tuple, Dict, Any

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


def is_identity_or_bot_query(text: str) -> bool:
    """
    Detects if the caller is asking whether the agent is an AI, bot, robot, real person, or human.
    e.g. 'are you a real person or ai', 'are you ai', 'kya aap robot ho', 'kya aap real person ho'
    These MUST be answered with the counselor identity and NOT trigger human escalation transfer.
    """
    t = text.lower().strip()
    t_clean = re.sub(r'[^\w\s]', ' ', t)
    t_clean = re.sub(r'\s+', ' ', t_clean).strip()

    identity_phrases = [
        "are you a real person", "are you real person", "are you a real human",
        "are you real human", "are you real", "are you human", "are you a human",
        "are you ai", "are you an ai", "are you a bot", "are you a robot",
        "are you an ai or a real person", "are you a real person or ai",
        "are you real person or ai", "are you real or ai", "are you ai or real",
        "are you human or ai", "are you ai or human", "are you bot or human",
        "am i speaking to a real person", "am i speaking to an ai", "am i speaking to a robot",
        "am i speaking to a bot", "am i talking to a real person", "am i talking to an ai",
        "am i talking to a robot", "am i talking to a bot", "is this an ai", "is this a robot",
        "is this a bot", "is this a real person",
        "kya aap real person ho", "kya aap real ho", "kya aap ai ho", "kya aap robot ho",
        "kya aap bot ho", "kya aap insaan ho", "kya aap human ho",
        "kya tum real person ho", "kya tum real ho", "kya tum ai ho", "kya tum robot ho",
        "aap real person ho ya ai", "aap ai ho ya real", "aap bot ho ya real",
        "aap insaan ho ya robot", "aap robot ho kya", "aap ai ho kya", "aap bot ho kya",
        "tum real person ho ya ai", "tum ai ho ya real", "tum insaan ho ya ai",
        "tum robot ho kya", "tum ai ho kya", "kya aap machine ho", "are you machine",
        "who are you", "who is this", "what is your name", "whats your name",
        "aap kaun ho", "kaun bol raha hai", "kisse baat ho rahi hai", "aapka naam kya hai"
    ]
    if any(p in t_clean for p in identity_phrases):
        return True

    # English patterns: 'are you an ai', 'you seem like an ai', 'i think you are a bot', 'talking to an ai'
    if re.search(r'\b(?:you\s+are|you\s+seem|you\s+sound|i\s+think\s+you|are\s+you|am\s+i\s+(?:speaking|talking)\s+to|is\s+this|talking\s+to)\b.*?\b(?:real\s+person|real\s+human|actual\s+person|actual\s+human|human|ai|robot|bot|machine|real)\b', t_clean):
        return True

    # Hindi patterns: 'aap ... ai / bot / robot ... ho', 'mujhe lagta hai aap ai ho', 'aap insaan ho ya robot'
    if re.search(r'\b(?:aap|tum)\b.*?\b(?:real|ai|robot|bot|machine|real\s+person|insaan|human)\b.*?\b(?:ho|lagte|lagti|hai|kya|ya)\b', t_clean):
        return True

    if re.search(r'\b(?:lagta\s+hai|lag\s+raha\s+hai)\b.*?\b(?:ai|robot|bot|machine)\b', t_clean):
        return True

    if re.search(r'\b(?:ai|robot|bot|real\s+person|insaan|machine)\b.*?\b(?:ho\s+kya|hai\s+kya|ya\s+ai|ya\s+real|ya\s+robot|ya\s+machine)\b', t_clean):
        return True

    return False


def detect_escalation_intent(user_text: str) -> Optional[Tuple[str, str]]:
    """
    High-threshold escalation detection.
    Does NOT escalate for routine questions (fees, pricing, placement, refund, tools, syllabus).
    ONLY escalates when:
    1. Caller explicitly and strongly insists on talking to a human/executive/counselor.
    2. Severe complaint, legal dispute, fraud accusation, or aggressive frustration.
    3. Aggressive commercial bargaining or demands for written salary/job guarantee.
    """
    t = user_text.lower().strip()

    # Guard: If caller is inquiring whether the agent is real/AI/bot, do NOT escalate
    if is_identity_or_bot_query(t):
        return None

    # 1. Direct intrinsic escalation entities (unambiguous intent to reach human authority)
    intrinsic_escalations = [
        "senior executive", "senior counsellor", "senior counselor", "senior manager",
        "senior advisor", "senior person", "talk to human", "talk to person",
        "speak to human", "speak to person", "talk to real person", "speak to real person",
        "live agent", "human agent", "human executive", "customer care executive",
        "higher authority", "team lead", "branch manager", "center head"
    ]
    if any(ent in t for ent in intrinsic_escalations):
        return ("executive_requested", "Let me connect you directly with our representative, please hold.")

    # 2. Flexible Transfer / Connect / Speak / Preposition Patterns (English & Hindi)
    transfer_actions = r"(?:transfer|connect|escalat|speak|talk|put\s+me\s+through|route|call|baat|karwao|karao|karva|to|with|give|pass|need|want|get)"
    target_entities = r"(?:human|person|agent|executive|counselor|counsellor|manager|advisor|team|admissions|billing|accounts|support|someone|anyone|staff|representative|supervisor|boss|insaan|aadmi|senior)"
    
    if re.search(rf"\b{transfer_actions}\b.*?\b{target_entities}\b", t, re.IGNORECASE) or re.search(rf"\b{target_entities}\b.*?\b{transfer_actions}\b", t, re.IGNORECASE):
        return ("executive_requested", "Let me connect you directly with our representative, please hold.")

    # Direct short requests
    direct_triggers = [
        "transfer my call", "transfer call", "transfer me", "transfer please", "call transfer",
        "transfer the call", "transfer this call", "escalate the call", "escalate this call",
        "escalate my call", "escalate call", "call escalate", "live agent", "talk to real person", "human agent",
        "human please", "talk to human", "speak to human", "give phone to", "phone do",
        "senior counselor", "human counselor", "need human", "want human",
        "senior executive", "senior person", "senior advisor",
        "to your senior", "to senior", "to your executive", "to senior executive",
        "to a human", "to the human", "to an agent", "to real human",
        "baat karao", "baat karni hai", "baat karni", "kisi se baat", "executive se baat",
        "counselor se baat", "human se baat", "phone kisi aur ko", "kisi human se",
        "aadmi se baat", "kisi se connect", "insaan se baat", "counselor se connect",
        "counsellor se baat", "call transfer karo", "transfer karo", "escalate karo",
        "senior se baat", "manager se baat", "sir se baat", "madam se baat"
    ]
    if any(k in t for k in direct_triggers):
        return ("executive_requested", "I understand. Let me connect you directly with our representative, please hold.")

    # 2. Heavy conversation: Severe complaints, legal disputes, fraud/consumer forum
    heavy_dispute_triggers = [
        "legal action", "consumer court", "consumer forum", "police complaint",
        "file a complaint", "fraud", "cheating", "sue", "lawyer",
        "scam", "worst service", "harassment"
    ]
    if any(k in t for k in heavy_dispute_triggers):
        return ("severe_dispute", "I take your concern very seriously. Let me connect you with our senior management right away, please hold.")

    # 3. Heavy conversation: Frustration with the AI
    ai_frustration_triggers = [
        "you are not understanding", "you dont understand", "you don't understand",
        "you are useless", "you know nothing", "you cannot answer", "you cant answer",
        "you can't answer", "stop repeating", "stop talking", "talk to your boss",
        "talk to manager", "let me speak to manager"
    ]
    if any(k in t for k in ai_frustration_triggers):
        return ("caller_frustrated", "I apologize for any difficulty. Let me put you through to our senior admissions counselor right away, please hold.")

    # 4. Demands for written guarantee or aggressive price bargaining
    aggressive_demands = [
        "guarantee in writing", "written guarantee", "negotiate fee right now", "give me discount now"
    ]
    if any(k in t for k in aggressive_demands):
        return ("aggressive_demand", "Fee negotiations and formal enrollments are handled by our senior counselor. Let me connect you now, please hold.")

    return None


def check_repeated_confusion(history: List, current_q: str, threshold: int = 4) -> bool:
    """Returns True if the exact same substantive question appeared 4 or more times."""
    clean_q = current_q.strip().lower()
    if len(clean_q) < 15:
        return False
    user_msgs = [h["content"].lower().strip() for h in history if h["role"] == "user"]
    count = sum(1 for m in user_msgs if m[:40] == clean_q[:40])
    return count >= threshold


def is_farewell_utterance(user_text: str) -> bool:
    """
    Detects if the caller is concluding the phone call (English & Hindi/Hinglish).
    Returns True ONLY if the caller is genuinely signing off and not asking a question.
    """
    t = user_text.lower().strip()
    words = set(re.findall(r'\b[a-z0-9]+\b', t))

    question_words = {
        "what", "how", "when", "where", "why", "who", "which", "fees", "fee", "cost", 
        "price", "duration", "syllabus", "placement", "salary", "certificate", 
        "internship", "batch", "batches", "timing", "timings", "tell", "kya", 
        "kitna", "kitni", "kaise", "kab", "kahan", "batao", "bataiye", "koun", "kaun"
    }
    if words & question_words:
        return False

    # 1. Exact short farewell acknowledgements and "by" / "bye" variants (Latin + Devanagari)
    clean_no_punct = "".join([c for c in t if c.isalnum() or c.isspace()]).strip()
    farewell_exact = {
        "by", "bye", "byee", "by by", "bye bye", "ok by", "ok bye", "okay by", "okay bye",
        "chalo by", "chalo bye", "thik hai by", "thik hai bye", "theek hai by", "theek hai bye",
        "thank you", "thanks", "thank you bye", "thank you by", "thanks bye", "thanks by",
        "thank you so much", "thanks a lot", "ok thank you", "okay thank you", "ok thanks", "okay thanks",
        "no thank you", "no thanks", "all good thank you", "all good thanks", "done thank you", "done thanks",
        "kuch nahi", "kuch nhi", "shukriya", "dhanyawad", "bas", "bas itna hi",
        "cut the call", "cut call", "disconnect", "hang up", "phone rakh do", "call cut kar do",
        # Devanagari exact signoffs
        "बाय", "बाय बाय", "ओके बाय", "अलविदा", "शुक्रिया", "धन्यवाद", "काफी है", "बस", "बस इतना ही",
        "कॉल कट", "फोन रख दो", "फोन काटो", "कॉल काटो"
    }
    if clean_no_punct in farewell_exact:
        return True

    # 2. Explicit farewell phrases (English, Latin Hinglish & Devanagari Hindi)
    farewell_phrases = [
        "bye", "goodbye", "good bye", "bye bye", "by by", "byee", "ok bye", "ok by", 
        "okay bye", "okay by", "no thanks bye", "no thanks by", "thank you bye", "thank you by", 
        "thanks bye", "thanks by", "thats all", "that is all", "thats it", "that is it", 
        "nothing else", "no nothing else", "talk to you later", "see you later", 
        "have a good day", "have a nice day", "cut the call", "cut call", "disconnect", 
        "hang up", "phone rakh", "phone rakh do", "call cut", "call kat", "call end",
        "kuch nahi chahiye", "bas itna hi", "thik hai bye", "thik hai by", "theek hai bye", 
        "theek hai by", "chalo bye", "chalo by", "baad me baat", "baad mein baat", "baad me call",
        "i will call back", "will call back", "call back later", "think about it",
        "soch ke", "soch kar", "aaj ke liye", "kaafi hai", "kapi hai",
        "thodi der me", "thodi der mein", "kaam aa gaya", "kaam hai", "call lagata hu", "call karta hu",
        "call you later", "call you back", "will call you", "busy hoon", "busy hu", "busy right now",
        "don't think so", "dont think so", "have all the information", "all the information", "all information",
        "all the information regarding that", "information regarding that", "egg count liter", "count liter",
        "i will call you later", "will call you later", "call later", "talk later",
        "no enough", "no no enough", "enough", "no more questions", "no other question", "no questions", "nothing more",
        "no need", "not needed", "leave it", "rehne do", "koi zaroorat nahi",
        "sab samajh gaya", "sab samajh aa gaya", "sab clear hai", "kuch nahi",
        # Devanagari phrases
        "बाय", "अलविदा", "आज के लिए", "काफी है", "बाद में बात", "बाद में कॉल",
        "सोच के", "सोच कर", "फोन रख", "कॉल कट", "कॉल काट", "बात करता हूँ", "बात करती हूँ",
        "थोड़ी देर में", "काम आ गया", "काम है", "कॉल लगाता हूँ", "कॉल लगाती हूँ", "कॉल करता हूँ", "कॉल करती हूँ",
        "बिजी हूँ", "बाद में फोन", "फिर बात", "फिर कॉल"
    ]
    for phrase in farewell_phrases:
        if phrase in t:
            return True

    return False


def is_agent_farewell(agent_text: str) -> bool:
    """
    Detects if the AI generated a farewell sentence (English or Hindi/Hinglish).
    Triggers automatic call hangup signaling to Asterisk/Exotel so calls never linger.
    """
    if not agent_text:
        return False
    at = agent_text.lower().strip()
    agent_farewell_patterns = [
        r"goodbye", r"\bbye\b", r"\balvida\b", r"have a (?:great|good|wonderful|nice) day",
        r"aapka din (?:accha|shubh|mangalamay|achha)", r"din accha guzre",
        r"thank you for calling", r"thank you for contacting", r"thanks for calling",
        r"take care", r"hamesha aapki madad ke liye yahan hain",
        r"phir kabhi baat", r"phir baat hogi", r"reach out anytime"
    ]
    for pat in agent_farewell_patterns:
        if re.search(pat, at, re.IGNORECASE):
            return True
    return False


# =========================================================================
# Off-Topic, Trolling & Trash Query Counter Guardrail
# =========================================================================

COURSE_BUSINESS_KEYWORDS = {
    "course", "courses", "digital marketing", "marketing", "fee", "fees", "cost", "price",
    "pricing", "syllabus", "curriculum", "placement", "placements", "job", "jobs", "salary",
    "internship", "batch", "batches", "admission", "admissions", "enroll", "enrollment",
    "timing", "timings", "duration", "month", "months", "certificate", "certification",
    "seo", "sem", "ads", "google ads", "facebook ads", "meta ads", "canva", "chatgpt",
    "tools", "classes", "class", "online", "offline", "center", "centre", "institute",
    "faculty", "trainer", "trainers", "teacher", "demo", "eligibility", "qualification",
    "learn", "study", "teach", "taught", "sikhate", "padhate", "sikhaoge",
    # Telephony / PhonoTel keywords
    "ivr", "telephony", "obd", "voicebot", "sip", "exotel", "cloud ivr", "virtual receptionist",
    "calling agent", "call center", "outbound", "inbound", "crm"
}


def is_off_topic_or_troll_query(user_text: str) -> Optional[Tuple[str, str]]:
    """
    Detects if the caller is asking garbage/trash questions, attempting prompt injections,
    asking for jokes/songs, flirting, or deliberately trying to mess with the AI.
    
    Returns Tuple[category, matched_phrase] or None if query is legitimate.
    """
    t = user_text.lower().strip()
    if not t:
        return None

    # 1. Clean punctuation for token checks
    t_clean = re.sub(r'[^\w\s]', ' ', t)
    t_clean = re.sub(r'\s+', ' ', t_clean).strip()
    words = set(t_clean.split())

    # 2. Never block bot identity questions (handled by counselor identity reply)
    if is_identity_or_bot_query(t):
        return None

    # 3. Never block legitimate business/course keywords
    if words & COURSE_BUSINESS_KEYWORDS:
        # Check if it's an explicit prompt injection despite containing a keyword
        if not any(inj in t for inj in ["ignore previous", "ignore all instructions", "system prompt", "jailbreak"]):
            return None

    # 4. Prompt Injection / Jailbreak Attacks
    injection_patterns = [
        r"ignore\s+(?:all\s+)?(?:previous\s+)?instructions",
        r"ignore\s+(?:everything|rules|above)",
        r"(?:what\s+is|show\s+me|tell\s+me|reveal)\s+(?:your\s+)?(?:system\s+prompt|prompt|instructions)",
        r"developer\s+(?:message|override|mode)",
        r"jailbreak",
        r"you\s+are\s+now\s+dan",
        r"dan\s+mode",
        r"do\s+anything\s+now",
        r"repeat\s+after\s+me",
        r"say\s+(?:i\s+am\s+stupid|i\s+am\s+a\s+bot|whatever\s+i\s+say)"
    ]
    for pat in injection_patterns:
        if re.search(pat, t, re.IGNORECASE):
            return ("prompt_injection", "Prompt injection or jailbreak attempt")

    # 5. Severe Vulgarity / Abusive Harassment
    abusive_patterns = [
        r"\b(?:madarchod|behenchod|bhenchod|bhosdike|bhosadike|chutiye?|gandu|gaand|lode|laude|randi|harami|kutte)\b",
        r"\b(?:fuck\s+you|fuck\s+off|motherfucker|asshole|bitch|bastard|piece\s+of\s+shit|shut\s+up\s+bitch)\b"
    ]
    for pat in abusive_patterns:
        if re.search(pat, t, re.IGNORECASE):
            return ("vulgarity_abuse", "Abusive language or verbal harassment")

    # 6. Entertainment, Songs, Singing & Performance
    if re.search(r"\b(?:sing\s+(?:a\s+)?(?:[a-z]+\s+)?song|sing\s+for\s+me|can\s+you\s+sing|gana\s+g[a-z]+|gaana\s+g[a-z]+|gana\s+sunao|gaana\s+sunao|kavita\s+sunao|poem\s+sunao|dance\s+karo|can\s+you\s+dance|rap\s+g[a-z]+|rap\s+sunao|rap\s+karo|beatbox|mimicry|story\s+sunao|bedtime\s+story|kahani\s+sunao|tell\s+me\s+a\s+story)\b", t, re.IGNORECASE):
        return ("entertainment", "Request for singing/dancing/storytelling")

    # 7. Jokes & Riddles
    if re.search(r"\b(?:joke|jokes|chutkula|chutkule|paheli|paheliyan|riddle|riddles)\b", t, re.IGNORECASE) or any(p in t_clean for p in ["make me laugh", "hasao mujhe"]):
        return ("jokes", "Request for jokes or riddles")

    # 8. Personal Inquiries, Flirting & Dating
    if re.search(r"\b(?:single\s+ho|will\s+you\s+marry\s+me|shadi\s+karog[ie]|shaadi\s+karog[ie]|do\s+you\s+love\s+me|pyar\s+kart[ie]|pyaar\s+kart[ie]|boyfriend|girlfriend|date\s+me|i\s+love\s+you|sexy|hot|sundar\s+ho|how\s+old\s+are\s+you|whats?\s+your\s+age|kya\s+pehna\s+hai|what\s+are\s+you\s+wearing|are\s+you\s+(?:a\s+)?(?:girl|boy)|are\s+you\s+single)\b", t, re.IGNORECASE):
        return ("flirting_personal", "Flirting or intrusive personal question")

    # 9. Out-of-Domain Tasks & General Trivia (Food ordering, weather, homework, politics, math)
    trivia_patterns = [
        r"\border\b.*?\b(?:pizza|burger|food|biryani)\b",
        r"\b(?:pizza|burger)\b",
        r"\b(?:khana\s+(?:bhej|order)|pizza\s+bhej|swiggy|zomato)\b",
        r"\b(?:weather\s+(?:in|today)|aaj\s+(?:ka\s+)?mausam)\b",
        r"\b(?:who\s+is\s+(?:the\s+)?(?:prime\s+minister|pm|president))\b",
        r"\b(?:who\s+won\s+(?:the\s+)?match|cricket\s+match\s+score|ipl\s+score|capital\s+of)\b",
        r"\b(?:solve\s+\d+|calculate|write\s+(?:python\s+)?code|write\s+an\s+essay|homework\s+kar\s+do)\b",
        r"\b(?:what\s+is\s+\d+\s*(?:\+|\-|\*|\/|plus|minus|multiplied\s+by)\s*\d+)\b"
    ]
    for pat in trivia_patterns:
        if re.search(pat, t, re.IGNORECASE):
            return ("out_of_domain_trivia", "Unrelated trivia or service request")

    # 10. Games, Roleplaying & Hypotheticals
    roleplay_game_patterns = [
        r"\b(?:pretend\s+(?:to\s+be|you\s+are)|act\s+as\s+(?:my\s+)?|roleplay|simulate|let's\s+play\s+a\s+game|play\s+a\s+game|khel\s+khelein|game\s+khelo|imagine\s+you\s+are)\b",
        r"\b(?:act\s+like\s+(?:a\s+)?(?:girlfriend|boyfriend|grandma|teacher|actor|dog|cat|robot))\b"
    ]
    for pat in roleplay_game_patterns:
        if re.search(pat, t, re.IGNORECASE):
            return ("roleplay_games", "Request for game playing or roleplaying")

    # 11. Counting, Alphabet & Repetition Testing
    testing_patterns = [
        r"\b(?:count\s+(?:from\s+)?\d+\s+to\s+\d+|count\s+to\s+\d+|ginti\s+sunao|pahada\s+sunao|tables?\s+sunao|abc\s+sunao|alphabet\s+sunao|say\s+hello\s+\d+\s+times)\b",
        r"\b(?:spell\s+[a-z]+|repeat\s+after\s+me|speak\s+like\s+a\s+robot)\b"
    ]
    for pat in testing_patterns:
        if re.search(pat, t, re.IGNORECASE):
            return ("testing_drills", "Artificial testing or counting drill")

    return None


def get_tenant_off_topic_reply(
    tenant_id: str,
    config: Optional[dict] = None,
    session_lang: str = "hi-IN",
    strike_count: int = 1,
    category: str = "off_topic"
) -> Tuple[str, bool]:
    """
    Produces a graduated deflection response for trash/garbage/trolling questions.
    Returns (spoken_reply, should_disconnect).
    
    Strike 1: Polite, warm deflection back to courses/business.
    Strike 2: Firm professional boundary warning.
    Strike 3+ or Abusive: Concludes and disconnects call.
    """
    lang = session_lang or "hi-IN"
    is_en = lang.startswith("en")
    cfg = config or {}
    b_name = cfg.get("business_name") or ("iDigital Studies" if "idigital" in tenant_id else "our team")
    advisor = cfg.get("business_rules", {}).get("agent_name") or ("Aanya" if "idigital" in tenant_id else "Rohan")

    # Severe abuse: immediate disconnect
    if category == "vulgarity_abuse":
        if is_en:
            return ("We do not tolerate abusive language on our desk. I am disconnecting this call now. Goodbye.", True)
        return ("Hum hamari helpline par ashabhya bhasha bardasht nahi karte. Main call disconnect kar rahi hoon. Alvida.", True)

    # Prompt injection: firm rejection
    if category == "prompt_injection":
        if is_en:
            return (f"I am {advisor} from {b_name}, and I can only assist you with our courses and admissions. How can I help with your studies today?", False)
        return (f"Main {b_name} se {advisor} hoon aur keval courses aur admissions ke baare mein guide kar sakti hoon. Batayein aap kya janna chahte hain?", False)

    # Strike 1: Warm redirection
    if strike_count <= 1:
        if "idigital" in tenant_id:
            if is_en:
                return (f"Haha, I am {advisor}, an admissions counselor at {b_name}! I am strictly here to help you with our digital marketing courses, fees, and placements. How can I assist your career goals today?", False)
            return (f"Haha, main {b_name} ki admissions counselor {advisor} hoon! Main yahan keval digital marketing courses, fees aur 100% placement support mein madad karti hoon. Batayein courses ke baare mein kya janna chahte hain?", False)
        else:
            if is_en:
                return (f"I am {advisor} from {b_name}. I can only assist with our business telephony solutions and services. How can I assist you with your business needs today?", False)
            return (f"Main {b_name} se {advisor} hoon aur sirf hamari business solutions ke baare mein jankari de sakta hoon. Aapko hamari services ke baare mein kya janna hai?", False)

    # Strike 2: Firm boundary
    elif strike_count == 2:
        if "idigital" in tenant_id:
            if is_en:
                return ("I can only answer questions related to our digital marketing courses and admissions on this helpline. Please let me know if you would like details on syllabus, batches, or fees.", False)
            return ("Main is helpline par sirf digital marketing courses aur admissions se jude sawalon ke jawab de sakti hoon. Kripya batayein kya aapko syllabus, batch timings ya fees ke baare mein janna hai?", False)
        else:
            if is_en:
                return (f"I can only assist with genuine {b_name} inquiries on this line. Please let me know how we can assist your business.", False)
            return (f"Main is call par sirf {b_name} ki services se jude sawalon mein madad kar sakta hoon. Kripya batayein aapki kya requirement hai.", False)

    # Strike 3+: Disconnect to save telephony budget
    else:
        if is_en:
            return (f"Since there are no course-related queries at this time, I will disconnect this call so other callers can connect. Feel free to call back whenever you'd like to explore our programs. Have a great day, goodbye!", True)
        return (f"Chunke abhi aapke paas courses se juda koi sawal nahi hai, isliye main call disconnect kar rahi hoon taaki doosre students connect kar sakein. Jab bhi aapko course ki jankari chahiye ho, aap dobara call kar sakte hain. Dhanyawad, alvida!", True)


# -----------------------------------------------------------------------------
# Brochure & Multi-Channel Dispatch Intent Detection
# -----------------------------------------------------------------------------
BROCHURE_KEYWORDS = [
    "brochure", "brocher", "brochar", "syllabus", "prospectus", "curriculum",
    "course details", "fees structure", "pdf", "course pdf", "document", "material"
]

SEND_KEYWORDS = [
    "send", "bhej", "share", "forward", "transfer", "give", "chahiye", "dedo",
    "daal do", "bhejo", "provide", "receive", "want", "karo", "kardo"
]

def detect_brochure_intent(user_text: str, last_agent_message: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Detects if the user wants to receive a brochure, syllabus, or course document
    via WhatsApp or Email.
    Returns dict with detected channel ('whatsapp', 'email', or None) or None.
    """
    if not user_text:
        return None

    t = user_text.lower().strip()
    t_clean = re.sub(r'[^\w\s]', ' ', t)
    t_clean = re.sub(r'\s+', ' ', t_clean).strip()

    is_negative = bool(re.search(r'\b(?:no|nope|not|dont|don\'t|nahi|nahin|na|kuch\s+nahi|nothing|cancel|stop|enough|bas)\b', t_clean))
    if is_farewell_utterance(user_text) or any(phrase in t_clean for phrase in ["dont think so", "don't think so", "have all the information", "all the information", "egg count liter", "call later", "talk later", "no enough", "enough"]):
        return None

    # Detect channel if mentioned (including common Indian English STT mishearings)
    has_whatsapp = bool(re.search(r'\b(?:whatsapp|whats\s*app|whats|wa|watsapp|what\'s\s*app|wats\s*app)\b', t_clean))
    has_email = bool(re.search(r'\b(?:email|mail|gmail|inbox|neil|male|image|mel|mil|nail|mill|g\s*mail)\b', t_clean))

    channel = None
    if has_whatsapp and not has_email:
        channel = "whatsapp"
    elif has_email and not has_whatsapp:
        channel = "email"

    # 1. Direct explicit brochure/document mention
    has_brochure_word = any(w in t_clean for w in BROCHURE_KEYWORDS)
    has_send_word = any(w in t_clean for w in SEND_KEYWORDS)

    # If the user says "no brochure" or "don't send brochure", do NOT trigger
    if is_negative and not has_send_word and not (has_whatsapp or has_email):
        return None

    if has_brochure_word and not (is_negative and not has_send_word):
        return {
            "channel": channel,
            "has_whatsapp": has_whatsapp,
            "has_email": has_email,
            "source": "brochure_keyword"
        }

    # 2. Channel + send request without explicit word "brochure" (e.g. "whatsapp pe bhej do", "mail me details")
    if (has_whatsapp or has_email) and has_send_word and not is_negative:
        return {
            "channel": channel,
            "has_whatsapp": has_whatsapp,
            "has_email": has_email,
            "source": "channel_send"
        }

    # 3. Contextual affirmative reply following agent OFFERING brochure
    if last_agent_message and not is_negative:
        last_low = last_agent_message.lower()
        has_doc_word = any(w in last_low for w in ["brochure", "syllabus", "curriculum", "course document", "pdf", "details on whatsapp", "details on email"])
        is_offering = has_doc_word and any(phrase in last_low for phrase in [
            "would you like", "should i send", "send you", "kya main",
            "bhej", "chahiye", "share the", "whatsapp or via email", "whatsapp or email", "whatsapp par ya email"
        ])
        is_already_sent = any(phrase in last_low for phrase in [
            "have sent the brochure", "sent the brochure", "bhej diya hai", "brochure send kar diya",
            "anything else about our courses"
        ])
        if is_offering and not is_already_sent:
            has_affirm = bool(re.search(r'\b(?:yes|yeah|yep|sure|please|haan|haanji|ha|send\s+it|bhej\s+do|bhej\s+dijiye|chahiye|send|kar\s+do)\b', t_clean))
            if has_affirm:
                return {
                    "channel": channel,
                    "has_whatsapp": has_whatsapp,
                    "has_email": has_email,
                    "source": "contextual_affirmation"
                }

    return None


def detect_confirmation_intent(user_text: str) -> Optional[bool]:
    """
    Detects confirmation / agreement (True) or rejection / denial (False)
    when the agent reads back a phone number or email address to confirm.
    """
    if not user_text:
        return None

    t = user_text.lower().strip()
    t_clean = re.sub(r'[^\w\s]', ' ', t)
    t_clean = re.sub(r'\s+', ' ', t_clean).strip()

    affirmative_patterns = [
        r"\b(?:yes|yeah|yep|yup|sure|correct|right|exact|exactly|true|perfect)\b",
        r"\b(?:haan|haanji|ha|hanji|han|sahi|sahi\s+hai|theek|theek\s+hai|bilkul|kar do|bhej do)\b",
        r"\b(?:confirm|confirmed|proceed|send\s+it|send|looks\s+good|yes\s+send)\b"
    ]
    rejection_patterns = [
        r"\b(?:no|nope|nah|not|wrong|incorrect|false|mistake|error)\b",
        r"\b(?:nahi|nahin|na|galat|galat\s+hai|badlo|change|different|other)\b",
        r"\b(?:not\s+this|wait|stop|cancel)\b"
    ]

    for pat in rejection_patterns:
        if re.search(pat, t_clean):
            return False

    for pat in affirmative_patterns:
        if re.search(pat, t_clean):
            return True

    return None

