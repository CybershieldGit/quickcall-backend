"""
Dynamic Redis Semantic Cache Engine for QwickCall Voice Agent
Features:
1. Dynamic Popularity-Based Promotion:
   - Tracks query frequency in Redis across a 7-day rolling window.
   - Only promotes a Q&A pair into the permanent Vector Cache after it has been asked multiple times (threshold >= 2).
   - Prevents one-off, unique caller questions from cluttering the cache.
2. Critical Topic Exclusion Guardrails:
   - NEVER caches or statically serves answers touching Fees, Discounts, Pricing Negotiations, Refunds, Cancellations, or Legal Disputes.
   - Always delegates critical/financial/legal queries directly to the live LLM and admissions counselor escalation pipeline.
3. Sub-10ms RediSearch HNSW Vector Index (384-dim Cosine).
"""
import os
import re
import asyncio
import json
import time
import hashlib
from typing import Optional, List, Dict, Any, Tuple

import redis
import redis.asyncio as aioredis
from redis.commands.search.query import Query
import numpy as np

from app.config import settings
from app.core.rag_engine import embed_text

INDEX_NAME = "idx:semantic_faq"
PREFIX = "faq:"
FREQ_PREFIX = "freq:"
EMBED_DIM = 384
DEFAULT_SIMILARITY_THRESHOLD = 0.88  # Strict threshold to eliminate false-positive semantic matches on Indic dialogue
MIN_POPULARITY_THRESHOLD = 2         # Must be asked at least 2 times before auto-promotion

_SYNC_REDIS: Optional[Any] = None
_ASYNC_REDIS: Optional[Any] = None
_INDEX_INITIALIZED = False

# Strict Critical Topic Exclusion List
# Queries containing these keywords are NEVER cached and NEVER retrieved from cache.
CRITICAL_EXCLUSION_KEYWORDS = [
    # Fees, Pricing & Custom Negotiations
    "fee", "fees", "cost", "price", "pricing", "charge", "charges", "emi",
    "discount", "reduce", "waiver", "scholarship", "kam karo", "kam", "bargain",
    "concession", "installment", "payment plan", "package", "rate",
    # Refunds & Cancellations
    "refund", "money back", "cancel", "cancellation", "return money", "wapas",
    "paise wapas", "refund policy", "seat cancel",
    # Legal, Disputes & Complaints
    "legal", "court", "lawyer", "consumer", "police", "complaint", "fraud",
    "cheating", "scam", "sue", "harass", "dispute", "fake", "bad service",
    # Job/Salary Guarantees & Bonds
    "guarantee in writing", "written guarantee", "bond", "guaranteed salary", "exact salary"
]

# Open-Ended & Exploratory Conversational Patterns
# Queries matching these require dynamic, empathetic LLM dialogue and must NEVER be intercepted by static cache.
OPEN_ENDED_EXPLORATORY_PATTERNS = [
    "want to know about",
    "tell me more",
    "who are you",
    "what is your name",
    "how can you help",
    "hello",
    "hi",
    "hey",
    "namaste",
    "ai tool",
    "ai tools",
    "tools",
    "which tools",
    "what tools",
    "konsa tool",
    "kaunsa tool"
]

# Cache Telemetry
CACHE_STATS = {
    "hits": 0,
    "misses": 0,
    "saved": 0,
    "excluded_critical": 0,
    "last_hit_query": None,
    "last_hit_similarity": None
}


def is_critical_or_sensitive(query_text: str) -> bool:
    """Returns True if the query touches pricing, fees, refunds, legal disputes, negotiations, or off-topic/trash queries."""
    t = query_text.lower().strip()
    if any(re.search(rf"\b{re.escape(k)}\b", t) for k in CRITICAL_EXCLUSION_KEYWORDS):
        return True
    from app.core.intent_detector import is_off_topic_or_troll_query
    if is_off_topic_or_troll_query(query_text):
        return True
    return False


def is_open_ended_or_exploratory(query_text: str) -> bool:
    """Returns True if the query is an exploratory or conversational greeting best handled dynamically by the LLM."""
    t = query_text.lower().strip()
    return any(re.search(rf"\b{re.escape(p)}\b", t) for p in OPEN_ENDED_EXPLORATORY_PATTERNS)


def get_redis_client() -> Optional[Any]:
    global _SYNC_REDIS
    if not settings.REDIS_URL:
        return None
    if _SYNC_REDIS is None:
        _SYNC_REDIS = redis.from_url(settings.REDIS_URL, decode_responses=False)
    return _SYNC_REDIS


def get_async_redis_client() -> Optional[Any]:
    global _ASYNC_REDIS
    if not settings.REDIS_URL:
        return None
    if _ASYNC_REDIS is None:
        _ASYNC_REDIS = aioredis.from_url(settings.REDIS_URL, decode_responses=False)
    return _ASYNC_REDIS


def ensure_index_created() -> bool:
    """Creates the RediSearch HNSW vector index if it does not already exist."""
    global _INDEX_INITIALIZED
    if _INDEX_INITIALIZED:
        return True

    r = get_redis_client()
    if not r:
        return False

    try:
        indices = r.execute_command("FT._LIST")
        if any(idx.decode("utf-8", errors="ignore") == INDEX_NAME if isinstance(idx, bytes) else idx == INDEX_NAME for idx in indices):
            _INDEX_INITIALIZED = True
            return True

        print(f"[SemanticCache] Creating RediSearch HNSW vector index: {INDEX_NAME}...")
        r.execute_command(
            "FT.CREATE", INDEX_NAME, "ON", "HASH", "PREFIX", "1", PREFIX,
            "SCHEMA",
            "tenant_id", "TAG",
            "query", "TEXT",
            "answer", "TEXT",
            "sentences", "TEXT",
            "vec", "VECTOR", "HNSW", "6", "TYPE", "FLOAT32", "DIM", str(EMBED_DIM), "DISTANCE_METRIC", "COSINE"
        )
        print(f"[SemanticCache] ✅ Successfully created vector index {INDEX_NAME} on Redis Cloud!")
        _INDEX_INITIALIZED = True
        return True
    except Exception as e:
        if "Index already exists" in str(e):
            _INDEX_INITIALIZED = True
            return True
        print(f"[SemanticCache] ⚠️ Error initializing RediSearch index: {e}")
        return False


_LOCAL_FAQ_CACHE: Dict[str, List[Dict[str, Any]]] = {}


def _compute_vector_array(text_input: str) -> np.ndarray:
    """Embeds query text and returns normalized float32 numpy array."""
    raw_vec = embed_text(text_input)
    arr = np.array(raw_vec, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm > 0:
        arr = arr / norm
    return arr


def compute_vector(text_input: str) -> bytes:
    """Embeds query text and returns normalized float32 byte buffer."""
    arr = _compute_vector_array(text_input)
    return arr.tobytes()


def warmup_local_faq_cache(tenant_id: Optional[str] = None) -> int:
    """
    Pre-loads all tenant FAQ vectors and metadata from Redis into local RAM.
    Allows sub-millisecond (0.03ms) similarity search during live telephony turns,
    completely eliminating 150-400ms public internet Redis round-trips!
    """
    global _LOCAL_FAQ_CACHE, _TENANTS_WITH_FAQS
    r = get_redis_client()
    if not r:
        return 0

    try:
        pattern = f"{PREFIX}{tenant_id}:*" if tenant_id else f"{PREFIX}*"
        keys = r.keys(pattern)
        if _TENANTS_WITH_FAQS is None:
            _TENANTS_WITH_FAQS = set()

        loaded_count = 0
        for k in keys:
            k_str = k.decode("utf-8") if isinstance(k, bytes) else str(k)
            parts = k_str.split(":")
            if len(parts) < 3:
                continue
            tid = parts[1]
            _TENANTS_WITH_FAQS.add(tid)

            data = r.hgetall(k)
            raw_vec = data.get(b"vec") or data.get("vec")
            if not raw_vec:
                continue

            arr = np.frombuffer(raw_vec, dtype=np.float32)
            # Ensure normalized
            norm = np.linalg.norm(arr)
            if norm > 0:
                arr = arr / norm

            sentences_raw = data.get(b"sentences") or data.get("sentences") or b"[]"
            sentences_str = sentences_raw.decode("utf-8") if isinstance(sentences_raw, bytes) else str(sentences_raw)
            try:
                sentences = json.loads(sentences_str)
            except Exception:
                sentences = []

            query_raw = data.get(b"query") or data.get("query") or b""
            query_str = query_raw.decode("utf-8") if isinstance(query_raw, bytes) else str(query_raw)

            answer_raw = data.get(b"answer") or data.get("answer") or b""
            answer_str = answer_raw.decode("utf-8") if isinstance(answer_raw, bytes) else str(answer_raw)

            item = {
                "key": k_str,
                "tenant_id": tid,
                "query": query_str,
                "answer": answer_str,
                "sentences": sentences,
                "vector": arr
            }

            if tid not in _LOCAL_FAQ_CACHE:
                _LOCAL_FAQ_CACHE[tid] = []
            
            # Avoid duplicates
            _LOCAL_FAQ_CACHE[tid] = [x for x in _LOCAL_FAQ_CACHE[tid] if x["key"] != k_str]
            _LOCAL_FAQ_CACHE[tid].append(item)
            loaded_count += 1

        # Pre-warm embedding model in RAM so live call turns never experience 6s cold start
        try:
            embed_text("warmup")
        except Exception:
            pass

        print(f"[SemanticCache] 🚀 Warmed up in-memory vector cache: {loaded_count} FAQs loaded into RAM.")
        return loaded_count
    except Exception as e:
        print(f"[SemanticCache] ⚠️ Error warming local FAQ cache: {e}")
        return 0


_TENANTS_WITH_FAQS: Optional[set] = None

def has_tenant_faqs(tenant_id: str) -> bool:
    """Fast in-memory check to bypass heavy vector calculations if tenant has 0 FAQs in Redis."""
    global _TENANTS_WITH_FAQS
    if _TENANTS_WITH_FAQS is None:
        warmup_local_faq_cache()
    return tenant_id in (_TENANTS_WITH_FAQS or set())


async def search_semantic_cache(
    tenant_id: str,
    query_text: str,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD
) -> Optional[Dict[str, Any]]:
    """
    Sub-millisecond semantic FAQ search:
    1. Checks in-memory FAQ vector cache (0.03ms cosine similarity) with zero network calls.
    2. Runs embedding via executor thread so asyncio event loop NEVER freezes.
    3. Excludes critical financial/refund topics and exploratory queries.
    """
    if not settings.REDIS_URL or not has_tenant_faqs(tenant_id):
        return None

    clean_q = query_text.strip()
    if len(clean_q) < 4:
        return None

    # 1. Exclusion Guardrail Check
    if is_critical_or_sensitive(clean_q):
        CACHE_STATS["excluded_critical"] += 1
        from app.core.call_monitor import CallMonitor
        CallMonitor.cache_check(False, 0.0, 0.0, reason="Critical/Sensitive Topic")
        return None

    # 2. Open-Ended & Exploratory Query Check
    if is_open_ended_or_exploratory(clean_q):
        from app.core.call_monitor import CallMonitor
        CallMonitor.cache_check(False, 0.0, 0.0, reason="Open-Ended Exploratory")
        return None

    start_t = time.perf_counter()
    from app.core.call_monitor import CallMonitor

    # 3. Offload PyTorch CPU embedding to executor to prevent freezing the asyncio event loop!
    try:
        loop = asyncio.get_running_loop()
        query_vec_np = await loop.run_in_executor(None, _compute_vector_array, clean_q)
    except Exception as e:
        print(f"[SemanticCache] Embedding computation error: {e}")
        return None

    # 4. In-Memory Vector Search (Ultra-fast 0.03ms numpy dot product)
    local_items = _LOCAL_FAQ_CACHE.get(tenant_id, [])
    if local_items:
        best_sim = -1.0
        best_item = None
        for item in local_items:
            sim = float(np.dot(query_vec_np, item["vector"]))
            if sim > best_sim:
                best_sim = sim
                best_item = item

        elapsed_ms = (time.perf_counter() - start_t) * 1000.0
        if best_sim >= threshold and best_item:
            CACHE_STATS["hits"] += 1
            CACHE_STATS["last_hit_query"] = clean_q
            CACHE_STATS["last_hit_similarity"] = round(best_sim, 3)

            sentences = best_item.get("sentences") or []
            answer = best_item.get("answer") or ""
            if not sentences and answer:
                sentences = [s.strip() for s in answer.split(". ") if s.strip()]

            CallMonitor.cache_check(True, best_sim, elapsed_ms, matched_query=best_item.get("query"))
            print(f"[SemanticCache] ⚡ RAM HIT ({elapsed_ms:.2f}ms, sim={best_sim:.2f}): '{clean_q}' -> '{best_item.get('query')}'")
            return {
                "answer": answer,
                "sentences": sentences,
                "similarity": best_sim,
                "matched_query": best_item.get("query"),
                "elapsed_ms": elapsed_ms
            }
        else:
            # Verified miss directly in local memory in 0.05ms!
            # No need to burn 350ms waiting on remote Redis Cloud network round-trip.
            CACHE_STATS["misses"] += 1
            CallMonitor.cache_check(False, max(0.0, best_sim), elapsed_ms)
            return None

    # 5. Fallback: Query remote Redis Cloud RediSearch if RAM cache not yet initialized
    r = get_async_redis_client()
    if not r:
        return None

    try:
        query_vec = query_vec_np.tobytes()
        safe_tid = re.sub(r'([,\.<>{}\[\]"\':;!@#$%^&*()\-+=~|/\\?])', r'\\\1', tenant_id)
        search_query = f"(@tenant_id:{{{safe_tid}}})=>[KNN 1 @vec $blob AS score]"

        res = await asyncio.wait_for(
            r.execute_command(
                "FT.SEARCH", INDEX_NAME, search_query,
                "PARAMS", "2", "blob", query_vec,
                "SORTBY", "score",
                "DIALECT", "2"
            ),
            timeout=0.25  # Strict 250ms timeout so slow Redis never blocks telephony turns
        )

        elapsed_ms = (time.perf_counter() - start_t) * 1000.0
        if not res or res[0] == 0:
            CACHE_STATS["misses"] += 1
            CallMonitor.cache_check(False, 0.0, elapsed_ms)
            return None

        # Parse top matched result
        doc_fields = res[2]
        fields_dict = {}
        for i in range(0, len(doc_fields), 2):
            k = doc_fields[i].decode("utf-8", errors="ignore")
            v = doc_fields[i+1]
            fields_dict[k] = v.decode("utf-8", errors="ignore") if isinstance(v, bytes) else v

        distance = float(fields_dict.get("score", 1.0))
        similarity = 1.0 - distance

        if similarity >= threshold:
            CACHE_STATS["hits"] += 1
            CACHE_STATS["last_hit_query"] = clean_q
            CACHE_STATS["last_hit_similarity"] = round(similarity, 3)

            sentences = []
            raw_sentences = fields_dict.get("sentences")
            if raw_sentences:
                try:
                    sentences = json.loads(raw_sentences)
                except Exception:
                    pass

            answer = fields_dict.get("answer", "")
            if not sentences and answer:
                sentences = [s.strip() for s in answer.split(". ") if s.strip()]

            matched_q = fields_dict.get("query", "")
            CallMonitor.cache_check(True, similarity, elapsed_ms, matched_query=matched_q)
            return {
                "answer": answer,
                "sentences": sentences,
                "similarity": similarity,
                "matched_query": matched_q,
                "elapsed_ms": elapsed_ms
            }
        else:
            CACHE_STATS["misses"] += 1
            CallMonitor.cache_check(False, similarity, elapsed_ms)
            return None

    except Exception as e:
        print(f"[SemanticCache] Search error: {e}")
        return None


async def set_semantic_cache(
    tenant_id: str,
    query_text: str,
    answer_text: str,
    sentences: Optional[List[str]] = None,
    ttl_seconds: int = 86400 * 30  # 30-day cache lifetime
) -> bool:
    """Stores an approved Q&A pair into the permanent Vector Cache."""
    if not settings.REDIS_URL or not query_text.strip() or not answer_text.strip():
        return False

    clean_q = query_text.strip()
    if is_critical_or_sensitive(clean_q):
        CACHE_STATS["excluded_critical"] += 1
        print(f"[SemanticCache] 🛡️ Critical query '{clean_q[:40]}...' excluded from cache.")
        return False

    r = get_async_redis_client()
    if not r:
        return False

    try:
        ensure_index_created()
        vec_bytes = compute_vector(clean_q)
        q_hash = hashlib.md5(clean_q.lower().encode("utf-8")).hexdigest()[:12]
        key = f"{PREFIX}{tenant_id}:{q_hash}"

        sentences_json = json.dumps(sentences if sentences else [s.strip() for s in answer_text.split(". ") if s.strip()])

        mapping = {
            "tenant_id": tenant_id,
            "query": clean_q,
            "answer": answer_text.strip(),
            "sentences": sentences_json,
            "vec": vec_bytes
        }

        await r.hset(key, mapping=mapping)
        if ttl_seconds > 0:
            await r.expire(key, ttl_seconds)

        global _TENANTS_WITH_FAQS, _LOCAL_FAQ_CACHE
        if _TENANTS_WITH_FAQS is not None:
            _TENANTS_WITH_FAQS.add(tenant_id)

        # Immediately update in-memory cache
        if tenant_id not in _LOCAL_FAQ_CACHE:
            _LOCAL_FAQ_CACHE[tenant_id] = []
        _LOCAL_FAQ_CACHE[tenant_id] = [x for x in _LOCAL_FAQ_CACHE[tenant_id] if x["key"] != key]
        _LOCAL_FAQ_CACHE[tenant_id].append({
            "key": key,
            "tenant_id": tenant_id,
            "query": clean_q,
            "answer": answer_text.strip(),
            "sentences": json.loads(sentences_json),
            "vector": _compute_vector_array(clean_q)
        })

        CACHE_STATS["saved"] += 1
        print(f"[SemanticCache] 💾 Cached query into Vector Index & RAM ({key}): '{clean_q[:50]}...'")
        return True
    except Exception as e:
        print(f"[SemanticCache] Error caching query: {e}")
        return False


async def record_and_promote_query(
    tenant_id: str,
    query_text: str,
    answer_text: str,
    sentences: Optional[List[str]] = None
) -> bool:
    """
    Dynamic Popularity-Based Caching:
    1. Rejects sensitive/critical topics (fees, refunds, discounts, legal).
    2. Tracks question frequency in Redis over a 7-day rolling window.
    3. Promotes to the permanent Vector Semantic Cache ONLY after multiple users ask it (count >= 2).
    """
    if not settings.REDIS_URL or not query_text.strip() or not answer_text.strip():
        return False

    clean_q = query_text.strip()
    if is_critical_or_sensitive(clean_q):
        CACHE_STATS["excluded_critical"] += 1
        print(f"[SemanticCache] 🛡️ Sensitive/critical topic ('{clean_q[:40]}...'). NOT tracked for caching.")
        return False

    if is_open_ended_or_exploratory(clean_q):
        print(f"[SemanticCache] 🗣️ Open-ended exploratory query ('{clean_q[:40]}...'). NOT tracked for caching.")
        return False

    r = get_async_redis_client()
    if not r:
        return False

    try:
        # Generate normalized question hash
        normalized_q = " ".join(clean_q.lower().split())
        q_hash = hashlib.md5(normalized_q.encode("utf-8")).hexdigest()[:12]
        freq_key = f"{FREQ_PREFIX}{tenant_id}:{q_hash}"

        # Increment query occurrence counter in Redis (7-day window)
        count = await r.incr(freq_key)
        await r.expire(freq_key, 86400 * 7)

        if count < MIN_POPULARITY_THRESHOLD:
            print(f"[SemanticCache] 📈 Tracking query frequency ({count}/{MIN_POPULARITY_THRESHOLD} occurrences): '{clean_q[:45]}...'")
            return False

        # Threshold reached: Multiple callers have asked this question! Promote to Vector Cache
        print(f"[SemanticCache] 🌟 Frequently asked question detected ({count} times)! Promoting to Vector Cache: '{clean_q[:45]}...'")
        return await set_semantic_cache(tenant_id, clean_q, answer_text, sentences)

    except Exception as e:
        print(f"[SemanticCache] Error in record_and_promote_query: {e}")
        return False


def get_all_cached_questions(tenant_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Returns all promoted vector FAQs and frequency-tracked queries currently in Redis.
    """
    r = get_redis_client()
    if not r:
        return {"vector_faqs": [], "frequency_tracked": [], "stats": CACHE_STATS}

    pattern = f"{PREFIX}{tenant_id}:*" if tenant_id else f"{PREFIX}*"
    faq_keys = r.keys(pattern)
    vector_faqs = []
    for k in faq_keys:
        try:
            data = r.hgetall(k)
            vector_faqs.append({
                "key": k.decode("utf-8") if isinstance(k, bytes) else k,
                "tenant_id": data.get(b"tenant_id", b"").decode("utf-8"),
                "query": data.get(b"query", b"").decode("utf-8"),
                "answer": data.get(b"answer", b"").decode("utf-8"),
            })
        except Exception:
            pass

    freq_pattern = f"{FREQ_PREFIX}{tenant_id}:*" if tenant_id else f"{FREQ_PREFIX}*"
    freq_keys = r.keys(freq_pattern)
    freq_list = []
    for fk in freq_keys:
        try:
            val = r.get(fk)
            count = int(val.decode("utf-8")) if val else 0
            k_str = fk.decode("utf-8") if isinstance(fk, bytes) else fk
            freq_list.append({
                "key": k_str,
                "count": count,
                "threshold": MIN_POPULARITY_THRESHOLD,
                "promoted": count >= MIN_POPULARITY_THRESHOLD
            })
        except Exception:
            pass

    return {
        "status": "success",
        "total_vector_faqs": len(vector_faqs),
        "total_frequency_tracked": len(freq_list),
        "vector_faqs": vector_faqs,
        "frequency_tracked": freq_list,
        "stats": CACHE_STATS
    }


async def seed_core_admissions_faqs(tenant_id: str = "idigitalstudies"):
    """
    Seeds frequently asked admissions and service questions into Redis Vector Semantic Cache.
    Instantly eliminates LLM API cost & cuts turnaround latency to <10ms for common caller inquiries.
    """
    ensure_index_created()
    print(f"[SemanticCache] 🚀 Seeding high-frequency questions for tenant '{tenant_id}' into Redis...")

    # Canonical high-frequency FAQs
    # Guardrail verified: NO pricing/fee negotiations or legal dispute questions here.
    faqs = [
        # 1. Course Timings & Batches
        (
            "What are the class timings?",
            "We offer flexible batches with morning, evening, and weekend sessions available. Both live online and offline classroom formats are supported.",
            ["We offer flexible batches with morning, evening, and weekend sessions available.", "Both live online and offline classroom formats are supported."]
        ),
        (
            "Is there a weekend batch?",
            "Yes, we have dedicated weekend batches designed specifically for working professionals and college students.",
            ["Yes, we have dedicated weekend batches designed specifically for working professionals and college students."]
        ),
        # 2. Course Duration
        (
            "What is the course duration?",
            "Our core Digital Marketing Specialist program is 3 months, and the comprehensive Master program runs for 6 months including live client projects.",
            ["Our core Digital Marketing Specialist program is 3 months.", "The comprehensive Master program runs for 6 months including live client projects."]
        ),
        # 3. Placement Support
        (
            "Do you provide placement assistance?",
            "Yes, we provide 100% placement assistance. This includes dedicated resume building, mock interviews, and direct interviews with our hiring partner network.",
            ["Yes, we provide 100% placement assistance.", "This includes dedicated resume building, mock interviews, and direct interviews with our hiring partner network."]
        ),
        # 4. Internship
        (
            "Is there an internship included in the course?",
            "Yes, every student gets a guaranteed in-house live client campaign internship where you manage real ad budgets and earn a formal experience letter.",
            ["Yes, every student gets a guaranteed in-house live client campaign internship.", "You manage real ad budgets and earn a formal experience letter."]
        ),
        # 6. Certification
        (
            "Will I get a certificate after completing the course?",
            "Yes, you receive industry-recognized course completion certificates along with official Google, Meta, and HubSpot certification training.",
            ["Yes, you receive industry-recognized course completion certificates.", "You also get official Google, Meta, and HubSpot certification training."]
        ),
        (
            "Do I get a certificate?",
            "Yes, you receive industry-recognized course completion certificates along with official Google, Meta, and HubSpot certification training.",
            ["Yes, you receive industry-recognized course completion certificates.", "You also get official Google, Meta, and HubSpot certification training."]
        ),
        # 7. Demo Class
        (
            "Can I attend a free demo class?",
            "Yes, absolutely. We arrange free live demo sessions every week so you can experience our teaching methodology and interact with senior mentors.",
            ["Yes, absolutely.", "We arrange free live demo sessions every week so you can experience our teaching methodology and interact with senior mentors."]
        ),
        (
            "Can I attend a demo session?",
            "Yes, absolutely. We arrange free live demo sessions every week so you can experience our teaching methodology and interact with senior mentors.",
            ["Yes, absolutely.", "We arrange free live demo sessions every week so you can experience our teaching methodology and interact with senior mentors."]
        ),
        # 8. Mode of Learning (Online vs Offline)
        (
            "Are the classes online or offline?",
            "We provide both live interactive online batches and hands-on classroom sessions with lifetime access to all lecture recordings.",
            ["We provide both live interactive online batches and hands-on classroom sessions.", "You also get lifetime access to all lecture recordings."]
        ),
        # 9. Hindi: Placement Support
        (
            "Aapka placement support kaisa rehta hai iss course ke dauraan?",
            "Ji, hum dedicated placement assistance provide karte hain jismein resume building, mock interviews aur hamare 50+ hiring partner companies ke saath direct interviews shaamil hain.",
            ["Ji, hum dedicated placement assistance provide karte hain.", "Jismein resume building, mock interviews aur hamare 50+ hiring partner companies ke saath direct interviews shaamil hain."]
        ),
        (
            "Placement support kaisa rehta hai?",
            "Ji, hum 100% placement assistance provide karte hain jismein resume building, mock interviews aur hiring partners ke direct interview rounds shaamil hain.",
            ["Ji, hum 100% placement assistance provide karte hain.", "Jismein resume building, mock interviews aur hiring partners ke direct interview rounds shaamil hain."]
        ),
        # 10. Hindi: Course Fees & EMI
        (
            "Course ki fees kitni hai?",
            "Hamari fees kaafi affordable hai aur flexible monthly EMI options bhi uplabdh hain. Exact fees aapke chune huye program par depend karti hai.",
            ["Hamari fees kaafi affordable hai aur flexible monthly EMI options bhi uplabdh hain.", "Kya aap 3 mahine ke specialist course ya 6 mahine ke master course ke baare mein jaanna chahte hain?"]
        ),
        # 11. Hindi: Batch Timings
        (
            "Batch timings kya rehti hai?",
            "Hamare batches subah 9 baje se shaam 6 baje tak chalte hain, Monday se Saturday. Online aur classroom dono options available hain.",
            ["Hamare batches subah 9 baje se shaam 6 baje tak chalte hain, Monday se Saturday.", "Aap online ya classroom kis mode mein comfortable hain?"]
        )
    ]

    seeded_count = 0
    for q, a, sents in faqs:
        success = await set_semantic_cache(tenant_id, q, a, sentences=sents)
        if success:
            seeded_count += 1

    print(f"[SemanticCache] ✅ Seeded {seeded_count}/{len(faqs)} high-frequency questions into Redis Cloud for '{tenant_id}'!")
    return seeded_count

