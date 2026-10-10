"""Knowledge Service — RAG Vector Retrieval, Document Processing & Semantic Cache"""
from app.services.knowledge.rag_engine import (
    get_tenant_knowledge_text,
    search_knowledge,
    embed_text,
)
from app.services.knowledge.pdf_processor import (
    extract_text_from_pdf,
    chunk_business_knowledge,
    parse_team_directory_with_llm,
)
from app.services.knowledge.semantic_cache import (
    search_semantic_cache,
    record_and_promote_query,
)

__all__ = [
    "get_tenant_knowledge_text",
    "search_knowledge",
    "embed_text",
    "extract_text_from_pdf",
    "chunk_business_knowledge",
    "parse_team_directory_with_llm",
    "search_semantic_cache",
    "record_and_promote_query",
]
