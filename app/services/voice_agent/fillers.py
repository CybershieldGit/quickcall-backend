"""
Conversational Fillers & Immediate Audio Acknowledgment Module
Pre-synthesizes and streams natural conversational filler words ('Sure!', 'Ji bilkul!', 'Alright!', 'Haanji bilkul!')
in ~0-50ms directly to the phone while Groq LLM + Sarvam TTS generate the full response in the background.
Eliminates all perceived latency on substantive caller queries.
"""
import re
import random
import asyncio
from typing import Dict, List, Optional, Tuple
from app.core.tts_engine import synthesize_pcm, chunk_pcm_audio, _detect_sarvam_language

# Pre-synthesized in-memory cache of 640-byte 8000Hz PCM chunks
FILLER_AUDIO_CACHE: Dict[str, List[bytes]] = {}

# Contextual filler groups:
# 1. English Fillers (Categorized by conversational context)
ENGLISH_FILLERS = [
    "Sure,",
    "Certainly,",
    "Right,",
    "Yes, absolutely,"
]

# 2. Hindi / Hinglish Fillers (Categorized by conversational context)
HINDI_FILLERS = [
    "Haanji,",
    "Ji bilkul,",
    "Ji zaroor,",
    "Ji,"
]

ALL_FILLERS = ENGLISH_FILLERS + HINDI_FILLERS

PLEASANTRIES_AND_GREETINGS = {
    "hello", "hi", "hey", "namaste", "good morning", "good afternoon", "good evening",
    "how are you", "how are you doing", "how do you do", "how r u", "how are u",
    "whats up", "what is up", "hope you are doing well", "how is it going",
    "thank you", "thanks", "thanks a lot", "thank you so much", "thank u",
    "welcome", "you are welcome", "nice to meet you", "good to talk to you"
}

SIMPLE_ACKS = {
    "ok", "okay", "got it", "understood", "i see", "alright", "sure", "fine", "cool",
    "great", "done", "yes", "yeah", "yep", "no", "nope", "not really", "correct", "right",
    "nothing", "no thanks", "not now", "later", "nevermind",
    "theek hai", "achha", "accha", "shukriya", "dhanyawad", "theek", "haanji"
}


async def warmup_fillers():
    """Pre-synthesizes all multilingual conversational fillers into RAM concurrently at startup."""
    from app.config import settings
    if not getattr(settings, "ENABLE_CONVERSATIONAL_FILLERS", False):
        print("⚡ [Fillers] Conversational fillers disabled (direct sub-400ms streaming mode active).")
        return

    global FILLER_AUDIO_CACHE

    async def _warm_one(text: str):
        try:
            pcm = await synthesize_pcm(text, target_sample_rate=8000)
            if pcm:
                FILLER_AUDIO_CACHE[text] = list(chunk_pcm_audio(pcm, chunk_size=640))
        except Exception as e:
            print(f"[Fillers] Error pre-warming filler '{text}': {e}")

    await asyncio.gather(*[_warm_one(t) for t in ALL_FILLERS], return_exceptions=True)
    print(f"✨ Conversational fillers pre-warmed into memory: {len(FILLER_AUDIO_CACHE)} contextual fillers ready for 0ms playback.")


def should_use_filler(caller_text: str, session: Optional[Dict] = None) -> bool:
    """
    Decides if caller utterance warrants an immediate conversational filler.
    Rules:
    - Disabled when ENABLE_CONVERSATIONAL_FILLERS is False (direct sub-400ms streaming mode).
    - Never fires on short acknowledgments, greetings, or clipped follow-ups (< 3 words).
    - Never fires back-to-back on consecutive turns (at most once every 2 turns) to prevent robotic repetition.
    - Never fires on audible checks, identity questions, or farewells.
    - Only fires on substantive, meaningful questions/inquiries.
    """
    from app.config import settings
    if not getattr(settings, "ENABLE_CONVERSATIONAL_FILLERS", False):
        return False

    if not caller_text or not caller_text.strip():
        return False

    clean = caller_text.strip().lower()
    cleaned_words = re.sub(r'[^a-zA-Z0-9\s]', '', clean).strip()
    words = cleaned_words.split()

    # Rule out short utterances (< 3 words): short follow-ups ("aur fees?", "timing kya?") sound
    # punchy and natural when answered immediately without a filler prefix.
    if len(words) < 3:
        return False

    # Anti-repetition: Prevent using fillers on back-to-back consecutive turns
    if session:
        curr_turn = session.get("turn_index", 0)
        last_turn = session.get("last_filler_turn", -99)
        if curr_turn - last_turn < 2:
            return False

        # Suppress fillers while brochure collection/confirmation is active
        brochure_ctx = session.get("brochure_context")
        if brochure_ctx and brochure_ctx.get("active"):
            return False

    # Skip if utterance contains email or phone / brochure indicators
    if "@" in caller_text or any(k in clean for k in ["email", "mail id", "gmail", "whatsapp", "phone number"]):
        return False

    # Skip on explicit language switch requests
    from app.core.voice_agent import detect_language_switch_intent
    if detect_language_switch_intent(caller_text):
        return False

    # Skip on greetings & pleasantries ("how are you", "thank you", etc.)
    if cleaned_words in PLEASANTRIES_AND_GREETINGS or any(cleaned_words == p for p in PLEASANTRIES_AND_GREETINGS):
        return False

    # Skip on simple acknowledgments / confirmations ("ok", "got it", "yes", etc.)
    if cleaned_words in SIMPLE_ACKS:
        return False

    # Skip on colloquial "how are you" variants
    if any(q in cleaned_words for q in [
        "how are you", "how do you do", "how are u", "how r u", "how is it going", "whats up",
        "kaise ho", "kaise hain", "kya hal hai"
    ]):
        return False

    # Skip on unclear / audible / repeat checks so we never awkwardly acknowledge bad audio
    if any(phrase in cleaned_words for phrase in [
        "audible", "hear me", "can you hear", "you there", "aawaz aa rahi hai",
        "aawaz nahi", "kya bola", "repeat", "pardon", "sunai nahi", "samajh nahi"
    ]):
        return False

    # Skip on identity queries
    if any(phrase in cleaned_words for phrase in ["your name", "who are you", "who is this", "who am i", "kaun ho", "koun ho", "naam kya"]):
        return False

    # Skip on farewells or disconnect requests
    from app.core.intent_detector import is_farewell_utterance, detect_escalation_intent
    if is_farewell_utterance(clean):
        return False

    # Skip on explicit human escalation requests
    if detect_escalation_intent(clean):
        return False

    # Substantive caller inquiry warrants an immediate natural conversational filler
    return True


def get_conversational_filler(caller_text: str, session: Optional[Dict] = None) -> Optional[Tuple[str, List[bytes]]]:
    """
    Intelligently selects a context-appropriate, non-random filler word matched to:
    1. Language: Hindi/Hinglish vs English
    2. Intent:
       - Confirmation / Possibility ("kya placement milega", "is there internship") -> "Ji bilkul," / "Yes, absolutely,"
       - Inquiry / Explanation ("kaisa rehta hai", "tell me about") -> "Ji zaroor," / "Certainly,"
       - Factual (Fees, Timings, Duration) -> "Haanji," / "Sure,"
       - General -> "Haanji," / "Right,"
    3. Anti-repetition: Never repeats the exact same filler word as the previous filler turn.
    """
    if not FILLER_AUDIO_CACHE:
        return None

    clean = caller_text.strip().lower()

    # Determine caller language from session or text
    lang = (session.get("language") if session else None) or _detect_sarvam_language(caller_text)
    is_hindi = bool(lang and lang.startswith("hi"))

    # Intent Detection:
    # 1. Confirmation / Possibility / Verification
    is_confirmation = any(pattern in clean for pattern in [
        "kya ", "milega", "milti hai", "milta hai", "hoga", "hoti hai", "hote hain",
        "kar sakte", "available hai", "possible hai", "provide karte", "guarantee",
        "can i", "is there", "do you provide", "do you offer", "will i get", "is it possible",
        "are there", "do you have"
    ])

    # 2. In-depth Inquiry / Explanation / Guidance
    is_explanation = any(pattern in clean for pattern in [
        "kaisa rehta hai", "kaise rehta hai", "kaise hota hai", "kaisa hota hai",
        "bataiye", "bata sakte", "jaanna", "jaankari", "explain", "detail",
        "tell me about", "could you explain", "what is included", "how does",
        "how do you", "what topics", "can you share"
    ])

    # 3. Specific Factual / Operational (Fees, Timing, Location, Duration)
    is_factual = any(pattern in clean for pattern in [
        "fees", "fee", "kitna kharcha", "paise", "cost", "price", "emi",
        "timing", "time", "kab se", "duration", "kitne mahine", "location", "address",
        "how much", "what time", "how long", "where is"
    ])

    # Select contextual filler
    if is_hindi:
        if is_confirmation:
            chosen = "Ji bilkul,"
        elif is_explanation:
            chosen = "Ji zaroor,"
        elif is_factual:
            chosen = "Haanji,"
        else:
            chosen = "Haanji,"
    else:
        if is_confirmation:
            chosen = "Yes, absolutely,"
        elif is_explanation:
            chosen = "Certainly,"
        elif is_factual:
            chosen = "Sure,"
        else:
            chosen = "Sure,"

    # Anti-repetition: If this filler matches the previous filler word, alternate to a natural synonym
    last_word = session.get("last_filler_word") if session else None
    if last_word and last_word == chosen:
        if is_hindi:
            alternates = {
                "Ji bilkul,": "Haanji,",
                "Ji zaroor,": "Haanji,",
                "Haanji,": "Ji zaroor,",
                "Ji,": "Haanji,"
            }
            chosen = alternates.get(chosen, "Haanji,")
        else:
            alternates = {
                "Yes, absolutely,": "Certainly,",
                "Certainly,": "Sure,",
                "Sure,": "Certainly,",
                "Right,": "Sure,"
            }
            chosen = alternates.get(chosen, "Sure,")

    # Fallback if chosen is not in cache
    if chosen not in FILLER_AUDIO_CACHE:
        fallback_pool = HINDI_FILLERS if is_hindi else ENGLISH_FILLERS
        for alt in fallback_pool:
            if alt in FILLER_AUDIO_CACHE:
                chosen = alt
                break

    chunks = FILLER_AUDIO_CACHE.get(chosen, [])
    if not chunks:
        return None

    if session:
        session["last_filler_turn"] = session.get("turn_index", 0)
        session["last_filler_word"] = chosen

    return (chosen, chunks)


def strip_duplicate_filler(reply: str) -> str:
    """
    Strips leading duplicate filler words or introductory phrases from the beginning of the LLM response
    so the filler word and the LLM body flow smoothly without awkward stuttering or repetition.
    """
    cleaned = re.sub(
        r'^(?:sure,?\s*|right,?\s*|certainly,?\s*|yes,\s*(?:absolutely,?)?\s*|definitely,?\s*|ji,?\s*(?:bilkul|zaroor)?\s*|haanji,?\s*(?:bilkul|zaroor)?\s*|haan\s*bilkul,?\s*|zaroor,?\s*|yes,?\s*|alright,?\s*)\s*[,!.-]?\s*',
        '',
        reply.strip(),
        flags=re.IGNORECASE
    ).strip()
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned if cleaned else reply

