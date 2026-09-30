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
    """Lazy-load or return cached sentence-transformers model."""
    global _embed_model
    if _embed_model is not None:
        return _embed_model
    try:
        from sentence_transformers import SentenceTransformer
        print("🧠 Pre-loading sentence-transformers all-MiniLM-L6-v2...")
        _embed_model = SentenceTransformer("all-MiniLM-L6-v2")
        print("✅ SentenceTransformer model ready.")
    except Exception as e:
        print(f"⚠️ sentence-transformers fallback: {e}")
        _embed_model = None
    return _embed_model


def _embed_with_tfidf(texts: List[str]) -> List[List[float]]:
    from sklearn.feature_extraction.text import TfidfVectorizer
    vec = TfidfVectorizer(max_features=384, sublinear_tf=True)
    matrix = vec.fit_transform(texts).toarray()
    return matrix.tolist()


def embed_text(text_input: str) -> List[float]:
    """Generate normalized embedding vector for a single text string."""
    model = _get_embed_model()
    if model:
        vec = model.encode(text_input, normalize_embeddings=True)
        return [float(x) for x in vec]
    else:
        vecs = _embed_with_tfidf([text_input])
        return vecs[0]


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

