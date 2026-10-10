"""
PDF Processor & Dynamic Ingestion Core
Extracts and semantically processes:
1. PDF 1: Business Knowledge Base (RAG chunks with vector embeddings)
2. PDF 2: Team & Escalation Directory (Department, Role, Phone routing parsed via LLM)
"""
import io
import re
import json
from typing import List, Dict, Any
from pypdf import PdfReader

from app.core import rag_engine



def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extracts clean text content from PDF binary bytes."""
    if not pdf_bytes or len(pdf_bytes) < 10:
        raise ValueError("Provided PDF file is empty or too small.")
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                raise ValueError("PDF is password protected. Please provide an unencrypted PDF.")
        full_text = []
        for page_idx, page in enumerate(reader.pages):
            page_text = page.extract_text() or ""
            clean_page = page_text.strip()
            if clean_page:
                full_text.append(clean_page)
        
        combined = "\n\n".join(full_text)
        # Normalize excessive whitespaces while preserving paragraph breaks
        combined = re.sub(r'[ \t]+', ' ', combined)
        combined = re.sub(r'\n{3,}', '\n\n', combined)
        return combined.strip()
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Failed to extract text from PDF: {str(e)}")


def chunk_business_knowledge(
    raw_text: str, 
    tenant_id: str, 
    source_doc: str = "business_knowledge.pdf",
    max_words_per_chunk: int = 180
) -> List[Dict[str, Any]]:
    """
    Splits business knowledge text into semantic chunks (~150-250 words)
    and computes normalized embedding vectors for pgvector RAG.
    """
    paragraphs = [p.strip() for p in raw_text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [p.strip() for p in raw_text.split("\n") if p.strip()]

    chunks = []
    current_words = []

    for para in paragraphs:
        para_words = para.split()
        if len(current_words) + len(para_words) <= max_words_per_chunk:
            current_words.extend(para_words)
        else:
            if current_words:
                chunk_str = " ".join(current_words).strip()
                if len(chunk_str) >= 30:
                    chunks.append(chunk_str)
            # If a single paragraph is longer than max_words, split into smaller blocks
            if len(para_words) > max_words_per_chunk:
                for i in range(0, len(para_words), max_words_per_chunk):
                    sub = " ".join(para_words[i:i + max_words_per_chunk]).strip()
                    if len(sub) >= 30:
                        chunks.append(sub)
                current_words = []
            else:
                current_words = list(para_words)

    if current_words:
        chunk_str = " ".join(current_words).strip()
        if len(chunk_str) >= 30:
            chunks.append(chunk_str)

    # Compute vector embeddings for each chunk
    results = []
    for idx, chunk_content in enumerate(chunks):
        try:
            vec = rag_engine.embed_text(chunk_content)
        except Exception as e:
            print(f"[PDF Processor] Embedding failed for chunk {idx}: {e}")
            vec = None

        results.append({
            "chunk_index": idx,
            "tenant_id": tenant_id,
            "source_doc": source_doc,
            "chunk_text": chunk_content,
            "embedding": vec
        })

    return results


def parse_team_directory_with_llm(raw_text: str) -> List[Dict[str, Any]]:
    """
    Uses Groq LLM to parse unstructured Team Directory text/PDF into
    a structured escalation routing table with departments and phone numbers.
    """
    from app.core.llm_service import (
        get_groq_client,
        GROQ_PRIMARY_MODEL,
        GROQ_FALLBACK_MODEL,
    )
    client = get_groq_client()
    system_prompt = (
        "You are an expert enterprise HR and telecom directory parser. "
        "Extract all teams, departments, staff members, and their direct contact phone numbers from the text. "
        "Format output STRICTLY as a valid JSON array of objects with keys: "
        "'department' (e.g. Admissions, Sales, Technical Support, Accounts, Billing, General), "
        "'name' (staff member name or department head), "
        "'role' (designation), "
        "'phone' (E.164 phone number with country code e.g. +91XXXXXXXXXX), "
        "'keywords' (list of 4-6 lowercase inquiry keywords matching this department's queries). "
        "DO NOT include markdown code blocks, explanations, or quotes. Output ONLY raw JSON array."
    )

    user_content = f"Here is the raw Team Directory text:\n\n{raw_text[:3500]}"

    if client:
        for model in [GROQ_PRIMARY_MODEL, GROQ_FALLBACK_MODEL]:
            try:
                res = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content}
                    ],
                    max_tokens=600,
                    temperature=0.1
                )
                output = res.choices[0].message.content.strip()
                # Strip any accidental ```json wrappers
                output = re.sub(r"^```json\s*", "", output, flags=re.IGNORECASE)
                output = re.sub(r"^```\s*", "", output)
                output = re.sub(r"\s*```$", "", output)
                parsed = json.loads(output)
                if isinstance(parsed, list) and len(parsed) > 0:
                    return parsed
            except Exception as e:
                print(f"[PDF Processor] Groq team parsing error on {model}: {e}")

    # Heuristic fallback: regex search for phone numbers and line contexts
    fallback_routes = []
    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    phone_pattern = re.compile(r'(\+?91[\s-]?[6-9]\d{9}|[6-9]\d{9})')

    for line in lines:
        match = phone_pattern.search(line)
        if match:
            raw_phone = re.sub(r'[\s-]', '', match.group(1))
            if not raw_phone.startswith('+'):
                raw_phone = '+91' + raw_phone if len(raw_phone) == 10 else '+' + raw_phone

            dept = "General Support"
            low_line = line.lower()
            if any(k in low_line for k in ["admiss", "course", "counsel"]):
                dept = "Admissions"
            elif any(k in low_line for k in ["tech", "support", "portal", "login"]):
                dept = "Technical Support"
            elif any(k in low_line for k in ["bill", "account", "fee", "refund"]):
                dept = "Accounts & Billing"

            fallback_routes.append({
                "department": dept,
                "name": line[:25].strip(),
                "role": "Representative",
                "phone": raw_phone,
                "keywords": [dept.lower().split()[0], "help", "queries"]
            })

    if not fallback_routes:
        fallback_routes = [{
            "department": "Admissions & Inquiries",
            "name": "Senior Counselor",
            "role": "Admissions Advisor",
            "phone": "+919560732274",
            "keywords": ["course", "fees", "admission", "details", "help"]
        }]

    return fallback_routes
