"""
RAG Engine — Embedding & Vector Search
Fast in-memory caching + sentence-transformers (all-MiniLM-L6-v2)
"""
import json
import numpy as np
from typing import List, Dict, Any, Optional
from sqlalchemy import text
from app.db.session import sync_engine

_embed_model = None
_TENANT_CHUNKS_CACHE: Dict[str, List[Dict[str, Any]]] = {}


def _get_embed_model():
    """Lazy-load or return cached sentence-transformers or fastembed model."""
    global _embed_model
    if _embed_model is not None:
        return _embed_model
    try:
        from sentence_transformers import SentenceTransformer
        print("🧠 Pre-loading sentence-transformers all-MiniLM-L6-v2...")
        _embed_model = SentenceTransformer("all-MiniLM-L6-v2")
        print("✅ SentenceTransformer model ready.")
        return _embed_model
    except Exception as e:
        print(f"⚠️ sentence-transformers not available: {e}. Trying fastembed...")
    
    try:
        from fastembed import TextEmbedding
        _embed_model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        print("✅ FastEmbed model ready.")
        return _embed_model
    except Exception as e:
        print(f"⚠️ fastembed not available: {e}")
        _embed_model = None
    return _embed_model


def _safe_hash_embedding(text_input: str, dim: int = 384) -> List[float]:
    """Lightweight pure-python deterministic fallback vector when no ML libraries are present."""
    import hashlib
    vec = [0.0] * dim
    words = text_input.lower().split()
    for w in words:
        h = int(hashlib.md5(w.encode("utf-8")).hexdigest(), 16)
        idx = h % dim
        vec[idx] += 1.0
    norm = sum(x * x for x in vec) ** 0.5
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


def embed_text(text_input: str) -> List[float]:
    """Generate normalized embedding vector for a single text string."""
    model = _get_embed_model()
    if model is not None:
        try:
            # SentenceTransformer
            if hasattr(model, "encode"):
                vec = model.encode(text_input, normalize_embeddings=True)
                return [float(x) for x in vec]
            # FastEmbed TextEmbedding
            elif hasattr(model, "embed"):
                generator = model.embed([text_input])
                vec = list(generator)[0]
                norm = float(np.linalg.norm(vec))
                if norm > 0:
                    vec = vec / norm
                return [float(x) for x in vec]
        except Exception as e:
            print(f"⚠️ Error during embedding generation: {e}")

    return _safe_hash_embedding(text_input, dim=384)


def warmup_rag(tenant_id: str = "idigitalstudies"):
    """
    Pre-warm the embedding model and cache all tenant chunks in RAM.
    Eliminates cold-start latency entirely during phone calls.
    """
    global _TENANT_CHUNKS_CACHE
    print("🚀 Warming up RAG engine...")
    _get_embed_model()
    # Dummy embedding to prime PyTorch / model weights
    embed_text("warmup")

    # Load and cache tenant chunks in RAM
    try:
        with sync_engine.connect() as conn:
            rows = conn.execute(
                text("SELECT chunk_id, source_doc, chunk_text, embedding FROM knowledge_chunks WHERE tenant_id = :tid"),
                {"tid": tenant_id}
            ).fetchall()

            if not rows:
                candidates = []
                if tenant_id.startswith("cl_idigital") or "digital" in tenant_id.lower() or tenant_id == "idigitalstudies":
                    candidates = ["cl_idigitalstudie_82d5", "idigitalstudies"]
                elif tenant_id.startswith("cl_phono") or "phono" in tenant_id.lower():
                    candidates = ["phonotel"]

                for cand in candidates:
                    if cand != tenant_id:
                        rows = conn.execute(
                            text("SELECT chunk_id, source_doc, chunk_text, embedding FROM knowledge_chunks WHERE tenant_id = :tid"),
                            {"tid": cand}
                        ).fetchall()
                        if rows:
                            break

        cached_list = []
        for chunk_id, source_doc, chunk_text, emb_data in rows:
            if not emb_data:
                continue
            # emb_data could be string or list
            emb_vec = json.loads(emb_data) if isinstance(emb_data, str) else emb_data
            emb_np = np.array(emb_vec, dtype=np.float32)
            norm = float(np.linalg.norm(emb_np))
            cached_list.append({
                "chunk_id": chunk_id,
                "source_doc": source_doc,
                "chunk_text": chunk_text,
                "vector": emb_np,
                "norm": norm
            })
        _TENANT_CHUNKS_CACHE[tenant_id] = cached_list
        print(f"✅ RAG warmed up! Cached {len(cached_list)} knowledge chunks in memory for '{tenant_id}'.")
    except Exception as e:
        print(f"⚠️ Failed to cache knowledge chunks: {e}")


def search_knowledge(tenant_id: str, query: str, top_k: int = 4) -> List[Dict[str, Any]]:
    """
    Retrieve top_k most relevant knowledge chunks in under 5ms using in-memory vectors.
    """
    global _TENANT_CHUNKS_CACHE
    chunks = _TENANT_CHUNKS_CACHE.get(tenant_id)
    if chunks is None:
        # Load cache if not already loaded
        warmup_rag(tenant_id)
        chunks = _TENANT_CHUNKS_CACHE.get(tenant_id, [])

    if not chunks:
        return []

    query_vec = np.array(embed_text(query), dtype=np.float32)
    norm_q = float(np.linalg.norm(query_vec))
    if norm_q == 0:
        return []

    scored = []
    for c in chunks:
        if c["norm"] == 0:
            continue
        score = float(np.dot(query_vec, c["vector"]) / (norm_q * c["norm"]))
        scored.append({
            "chunk_id": c["chunk_id"],
            "source_doc": c["source_doc"],
            "chunk_text": c["chunk_text"],
            "score": score
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]

_TENANT_TEXT_CACHE: Dict[str, str] = {}


def get_tenant_knowledge_text(tenant_id: str) -> str:
    """
    Returns all approved facts for the tenant cached directly in RAM.
    Operates in 0ms without loading heavy SentenceTransformer PyTorch models.
    """
    global _TENANT_TEXT_CACHE
    if tenant_id in _TENANT_TEXT_CACHE and _TENANT_TEXT_CACHE[tenant_id] is not None:
        return _TENANT_TEXT_CACHE[tenant_id]

    # Fast direct SQL load without PyTorch overhead
    try:
        with sync_engine.connect() as conn:
            rows = conn.execute(
                text("SELECT chunk_text FROM knowledge_chunks WHERE tenant_id = :tid"),
                {"tid": tenant_id}
            ).fetchall()

            # If no rows found, check normalized tenant or related aliases
            if not rows:
                candidates = []
                if tenant_id.startswith("cl_idigital") or "digital" in tenant_id.lower() or tenant_id == "idigitalstudies":
                    candidates = ["cl_idigitalstudie_82d5", "idigitalstudies"]
                elif tenant_id.startswith("cl_phono") or "phono" in tenant_id.lower():
                    candidates = ["phonotel"]

                for cand in candidates:
                    if cand != tenant_id:
                        rows = conn.execute(
                            text("SELECT chunk_text FROM knowledge_chunks WHERE tenant_id = :tid"),
                            {"tid": cand}
                        ).fetchall()
                        if rows:
                            break

            text_content = "\n".join([f"• {r[0]}" for r in rows if r[0]]) if rows else ""
            _TENANT_TEXT_CACHE[tenant_id] = text_content
            return text_content
    except Exception as e:
        print(f"[RAG] Knowledge text fetch error: {e}")

    fallback = f"Advising and support facts for {tenant_id}."
    _TENANT_TEXT_CACHE[tenant_id] = fallback
    return fallback


def warmup_all_tenants():
    """Warms up RAG embedding model and pre-caches chunks for all active tenants."""
    print("🚀 Pre-warming RAG engine for all active tenants...")
    _get_embed_model()
    embed_text("warmup")
    try:
        with sync_engine.connect() as conn:
            rows = conn.execute(text("SELECT DISTINCT tenant_id FROM tenants WHERE status = 'active'")).fetchall()
            active_tids = [r[0] for r in rows if r[0]]
            if not active_tids:
                print("ℹ️ No active tenants found in database to pre-warm.")
                return
            for tid in active_tids:
                warmup_rag(tid)
    except Exception as e:
        print(f"⚠️ Error warming up all tenants: {e}")


def invalidate_tenant_rag_cache(tenant_id: str):
    """Invalidates and immediately re-warms cached chunks for a specific tenant after new PDF uploads."""
    global _TENANT_CHUNKS_CACHE, _TENANT_TEXT_CACHE
    _TENANT_CHUNKS_CACHE.pop(tenant_id, None)
    _TENANT_TEXT_CACHE.pop(tenant_id, None)
    warmup_rag(tenant_id)
    get_tenant_knowledge_text(tenant_id)
    print(f"🔄 Invalidated and refreshed RAG knowledge cache for '{tenant_id}'.")


