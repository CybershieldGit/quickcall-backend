"""
Tenant Portal & SaaS Router
Handles:
- Vendor self-serve registration & demo authentication
- Hybrid Telephony Settings (Master Pool vs BYOC)
- Dual-PDF Ingestion (PDF 1: Business RAG, PDF 2: Team Directory)
- Assigned Virtual DIDs management
- Custom tenant integration README documentation
"""
import re
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import sync_engine
from app.core.pdf_processor import (
    extract_text_from_pdf, chunk_business_knowledge, parse_team_directory_with_llm
)
from app.core import rag_engine
from app.core.tenant_service import _TENANT_CONFIG_CACHE, clear_did_cache, clear_agent_cache
from app.core.auth import create_access_token, get_current_tenant_auth, verify_tenant_access
import ipaddress
import socket

def is_safe_external_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        if hostname.lower() in ('localhost', '127.0.0.1', '::1', 'metadata.google.internal'):
            return False
        addr_info = socket.getaddrinfo(hostname, None)
        for item in addr_info:
            ip_str = item[4][0]
            ip_obj = ipaddress.ip_address(ip_str)
            if (
                ip_obj.is_loopback
                or ip_obj.is_private
                or ip_obj.is_link_local
                or ip_obj.is_reserved
                or ip_obj.is_multicast
            ):
                return False
        return True
    except Exception:
        return False


router = APIRouter(prefix="/api/v1/tenant", tags=["Tenant SaaS Portal"])


# -----------------------------------------------------------------------------
# Request & Response Models
# -----------------------------------------------------------------------------
class TenantRegisterRequest(BaseModel):
    business_name: str
    email: str
    plan: str = "starter"  # starter | growth | enterprise
    telephony_mode: str = "master_pool"  # master_pool | byoc
    did_number: Optional[str] = None  # Optional RP Digital DID provided by enterprise


class TelephonySettingsRequest(BaseModel):
    tenant_id: str
    telephony_mode: str  # master_pool | byoc
    exotel_account_sid: Optional[str] = None
    exotel_api_key: Optional[str] = None
    exotel_api_token: Optional[str] = None
    exotel_caller_id: Optional[str] = None


class AssignNumberRequest(BaseModel):
    tenant_id: str
    phone_number: str
    vendor: Optional[str] = "exotel"
    agent_id: Optional[str] = None


class UnassignNumberRequest(BaseModel):
    tenant_id: str
    phone_number: str


class AgentCreateRequest(BaseModel):
    name: str
    role_title: str = "AI Agent"
    persona: str
    greeting: str
    escalation_number: Optional[str] = None
    voice_id: Optional[str] = "default"
    language: Optional[str] = "hi-IN"
    is_default: Optional[bool] = False
    assign_to_did: Optional[str] = None


class AgentUpdateRequest(BaseModel):
    name: Optional[str] = None
    role_title: Optional[str] = None
    persona: Optional[str] = None
    greeting: Optional[str] = None
    escalation_number: Optional[str] = None
    voice_id: Optional[str] = None
    language: Optional[str] = None
    is_default: Optional[bool] = None


class AssignAgentToDIDRequest(BaseModel):
    tenant_id: str
    phone_number: str
    agent_id: str


class WebsiteScrapeRequest(BaseModel):
    tenant_id: str
    website_url: str


class LeadStatusUpdateRequest(BaseModel):
    status: str  # hot | warm | cold | callback | converted


class LeadCreateRequest(BaseModel):
    tenant_id: str
    name: Optional[str] = "Inbound Lead"
    phone: str
    reason: Optional[str] = ""
    status: Optional[str] = "hot"


# -----------------------------------------------------------------------------
# 1. Tenant Registration & Demo Login (DID-based Multi-Tenant Model)
# -----------------------------------------------------------------------------
@router.post("/register")
async def register_tenant(req: TenantRegisterRequest):
    """
    Registers a new brand/vendor on the platform.
    The Tenant UID is directly assigned by their RP Digital DID phone number.
    Allocates an available DID from phone_number_pool if not explicitly provided.
    """
    assigned_did = None
    with sync_engine.connect() as conn:
        # 1. Determine DID number: from request or allocate from pool
        if req.did_number and req.did_number.strip():
            assigned_did = req.did_number.strip()
        else:
            # Query the next available number from the RP Digital pool
            pool_row = conn.execute(
                text("""
                    SELECT number FROM phone_number_pool 
                    WHERE status = 'available' AND vendor = 'rpdigital' 
                    LIMIT 1
                """)
            ).fetchone()
            if pool_row:
                assigned_did = pool_row[0]
            else:
                # Dynamic fallback virtual DID if pool empty
                slug_num = "".join(filter(str.isdigit, req.business_name))[:6] or uuid.uuid4().hex[:6]
                assigned_did = f"+9173{slug_num.ljust(8, '0')[:8]}"

        # 2. Assign Tenant UID by their DID number
        clean_digits = "".join(filter(str.isdigit, assigned_did))
        tenant_id = f"did_{clean_digits}" if clean_digits else f"did_{uuid.uuid4().hex[:8]}"

        # Generate secure tenant API key
        api_key = f"qck_live_{uuid.uuid4().hex}"

        # 3. Check if tenant already exists by email or tenant_id
        existing = conn.execute(
            text("SELECT tenant_id, api_key FROM tenants WHERE tenant_id = :tid OR email = :email"),
            {"tid": tenant_id, "email": req.email}
        ).fetchone()

        if existing:
            tenant_id = existing[0]
            api_key = existing[1] or api_key
            conn.execute(
                text("""
                    UPDATE tenants 
                    SET business_name = :name, plan = :plan, telephony_mode = :mode, api_key = COALESCE(api_key, :key)
                    WHERE tenant_id = :tid
                """),
                {"name": req.business_name, "plan": req.plan, "mode": req.telephony_mode, "key": api_key, "tid": tenant_id}
            )
            conn.commit()
        else:
            # Insert new tenant with DID-based UID
            conn.execute(
                text("""
                    INSERT INTO tenants (tenant_id, business_name, email, api_key, plan, telephony_mode, status)
                    VALUES (:tid, :name, :email, :key, :plan, :mode, 'active')
                """),
                {
                    "tid": tenant_id,
                    "name": req.business_name,
                    "email": req.email,
                    "key": api_key,
                    "plan": req.plan,
                    "mode": req.telephony_mode
                }
            )

            # Insert default TenantConfig with dynamic persona
            default_persona = (
                f"You are a helpful, professional customer advisor for {req.business_name}. "
                f"Answer caller questions accurately, warmly, and concisely in 1-2 spoken sentences."
            )
            conn.execute(
                text("""
                    INSERT INTO tenant_config (config_id, tenant_id, version, persona, business_rules, escalation_number, is_active)
                    VALUES (:cid, :tid, 1, :persona, :rules, :esc, true)
                    ON CONFLICT DO NOTHING
                """),
                {
                    "cid": uuid.uuid4().hex,
                    "tid": tenant_id,
                    "persona": default_persona,
                    "rules": '{"team_routing": []}',
                    "esc": settings.DEFAULT_ESCALATION_NUMBER
                }
            )

            # Bind DID to tenant in tenant_phone_numbers
            conn.execute(
                text("""
                    INSERT INTO tenant_phone_numbers (number, tenant_id, vendor, is_active)
                    VALUES (:num, :tid, 'rpdigital', true)
                    ON CONFLICT (number) DO UPDATE SET tenant_id = :tid, vendor = 'rpdigital', is_active = true
                """),
                {"num": assigned_did, "tid": tenant_id}
            )

            # Mark number as assigned in phone_number_pool
            conn.execute(
                text("""
                    INSERT INTO phone_number_pool (number, vendor, status, assigned_tenant_id)
                    VALUES (:num, 'rpdigital', 'assigned', :tid)
                    ON CONFLICT (number) DO UPDATE SET status = 'assigned', assigned_tenant_id = :tid
                """),
                {"num": assigned_did, "tid": tenant_id}
            )
            conn.commit()

    return {
        "status": "success",
        "message": f"Tenant '{req.business_name}' successfully provisioned with DID-assigned UID!",
        "tenant_id": tenant_id,
        "assigned_did": assigned_did,
        "api_key": api_key,
        "business_name": req.business_name,
        "plan": req.plan,
        "telephony_mode": req.telephony_mode
    }


@router.post("/login")
async def login_tenant(tenant_id: str = Form(...)):
    """Demo login to retrieve tenant workspace."""
    with sync_engine.connect() as conn:
        tenant = conn.execute(
            text("SELECT tenant_id, business_name, email, plan, telephony_mode, status FROM tenants WHERE tenant_id = :tid OR business_name ILIKE :tid LIMIT 1"),
            {"tid": tenant_id.strip()}
        ).fetchone()

        if not tenant:
            raise HTTPException(status_code=404, detail=f"Vendor workspace '{tenant_id}' not found.")

        # Fetch assigned phone numbers
        numbers = conn.execute(
            text("SELECT number, vendor, is_active FROM tenant_phone_numbers WHERE tenant_id = :tid"),
            {"tid": tenant[0]}
        ).fetchall()

        # Fetch knowledge count
        k_count = conn.execute(
            text("SELECT COUNT(*) FROM knowledge_chunks WHERE tenant_id = :tid"),
            {"tid": tenant[0]}
        ).scalar() or 0

        # Fetch team routing rules
        cfg = conn.execute(
            text("SELECT business_rules FROM tenant_config WHERE tenant_id = :tid ORDER BY version DESC LIMIT 1"),
            {"tid": tenant[0]}
        ).fetchone()
        
        team_routing = []
        if cfg and cfg[0]:
            import json
            rules_data = cfg[0] if isinstance(cfg[0], dict) else json.loads(cfg[0])
            team_routing = rules_data.get("team_routing", [])

    return {
        "status": "success",
        "token": create_access_token({"sub": tenant[0], "tenant_id": tenant[0], "role": "tenant"}),
        "tenant": {
            "tenant_id": tenant[0],
            "business_name": tenant[1],
            "email": tenant[2],
            "plan": tenant[3],
            "telephony_mode": tenant[4],
            "status": tenant[5]
        },
        "assigned_numbers": [{"number": n[0], "vendor": n[1], "is_active": n[2]} for n in numbers],
        "knowledge_chunks_count": k_count,
        "team_routing": team_routing
    }



tenant_guard = APIRouter(dependencies=[Depends(get_current_tenant_auth)])

# -----------------------------------------------------------------------------
# 2. Dual-PDF Ingestion (Slot 1: Overview, Slot 2: Product Catalog) & Website Ingestion
# -----------------------------------------------------------------------------
@tenant_guard.post("/upload-knowledge-pdf")
async def upload_knowledge_pdf(
    tenant_id: str = Form(...),
    file: UploadFile = File(...),
    slot: Optional[str] = Form("slot1")  # slot1 (Overview) | slot2 (Catalog) | custom
):
    """
    Ingests Business Knowledge PDF into Dual-PDF Slots:
    - Slot 1: Company Overview, policies, FAQ, terms, hours of operation.
    - Slot 2: Product & Service Catalog, pricing tiers, SKU details.
    Extracts text, builds semantic chunks (~200 words), computes vector embeddings,
    and stores them strictly isolated under this tenant_id in pgvector.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported for Knowledge Base ingestion.")

    content = await file.read()
    if len(content) < 100:
        raise HTTPException(status_code=400, detail="Uploaded PDF file is empty or corrupted.")

    # 1. Extract text from PDF
    try:
        raw_text = extract_text_from_pdf(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to extract PDF text: {str(e)}")

    if not raw_text or len(raw_text.strip()) < 50:
        raise HTTPException(status_code=400, detail="No readable text found in PDF. Make sure it's not a scanned image PDF.")

    slot_label = "Slot 1 (Overview)" if slot == "slot1" else ("Slot 2 (Catalog)" if slot == "slot2" else f"Slot ({slot})")
    doc_identifier = f"[{slot_label}] {file.filename}"

    # 2. Chunk text and compute embeddings
    chunks = chunk_business_knowledge(raw_text, tenant_id=tenant_id, source_doc=doc_identifier)
    if not chunks:
        raise HTTPException(status_code=400, detail="Could not create knowledge chunks from document.")

    # 3. Store into knowledge_chunks table (replace previous chunks for this slot or doc)
    import json
    with sync_engine.connect() as conn:
        slot_pattern = f"[{slot_label}]%"
        conn.execute(
            text("DELETE FROM knowledge_chunks WHERE tenant_id = :tid AND (source_doc LIKE :pat OR source_doc = :doc)"),
            {"tid": tenant_id, "pat": slot_pattern, "doc": file.filename}
        )

        for c in chunks:
            cid = uuid.uuid4().hex
            emb_json = json.dumps(c["embedding"]) if c["embedding"] else None
            conn.execute(
                text("""
                    INSERT INTO knowledge_chunks (chunk_id, tenant_id, source_doc, chunk_text, embedding)
                    VALUES (:cid, :tid, :doc, :txt, :emb)
                """),
                {
                    "cid": cid,
                    "tid": tenant_id,
                    "doc": c["source_doc"],
                    "txt": c["chunk_text"],
                    "emb": emb_json
                }
            )
        conn.commit()

    # Invalidate in-memory caches and immediately re-warm RAM vectors for zero cold-start latency
    rag_engine.invalidate_tenant_rag_cache(tenant_id)

    return {
        "status": "success",
        "message": f"Successfully ingested {len(chunks)} knowledge chunks for {slot_label} under tenant '{tenant_id}'.",
        "chunks_indexed": len(chunks),
        "slot": slot,
        "source_doc": doc_identifier,
        "sample_chunks": [c["chunk_text"][:120] + "..." for c in chunks[:3]]
    }


@tenant_guard.post("/scrape-website")
async def scrape_website_knowledge(req: WebsiteScrapeRequest):
    """
    Website & Sitemap Ingestion:
    Fetches the company website, extracts clean text, computes vector embeddings,
    and indexes them into the tenant's RAG knowledge base.
    """
    url = req.website_url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    if not is_safe_external_url(url):
        raise HTTPException(
            status_code=400,
            detail="Forbidden URL. Localhost, private RFC1918 subnets, and metadata endpoints are prohibited."
        )

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            headers = {"User-Agent": "QwickCall-AI-Crawler/1.0 (+https://qwickcall.ai)"}
            res = await client.get(url, headers=headers)
            if res.status_code != 200:
                raise HTTPException(status_code=400, detail=f"Failed to fetch website (HTTP {res.status_code})")
            html_text = res.text
    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail=f"Website request timed out while fetching {url}")
    except httpx.RequestError as re:
        raise HTTPException(status_code=400, detail=f"Failed to connect to website: {str(re)}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Website fetch error: {str(e)}")

    # Clean HTML: remove script/style tags and extract text
    cleaned_html = re.sub(r'<(script|style|noscript)[^>]*>.*?</\1>', ' ', html_text, flags=re.DOTALL | re.IGNORECASE)
    clean_text = re.sub(r'<[^>]+>', ' ', cleaned_html)
    clean_text = re.sub(r'[ \t]+', ' ', clean_text)
    clean_text = re.sub(r'\n+', '\n', clean_text).strip()

    if len(clean_text) < 100:
        raise HTTPException(status_code=400, detail="Could not extract sufficient text from the website URL.")

    parsed = urlparse(url)
    doc_label = f"[Website] {parsed.netloc}{parsed.path[:20]}"
    chunks = chunk_business_knowledge(clean_text[:15000], tenant_id=req.tenant_id, source_doc=doc_label)

    if not chunks:
        raise HTTPException(status_code=400, detail="Could not chunk website content.")

    import json
    with sync_engine.connect() as conn:
        conn.execute(
            text("DELETE FROM knowledge_chunks WHERE tenant_id = :tid AND source_doc LIKE '[Website]%'"),
            {"tid": req.tenant_id}
        )
        for c in chunks:
            cid = uuid.uuid4().hex
            emb_json = json.dumps(c["embedding"]) if c["embedding"] else None
            conn.execute(
                text("""
                    INSERT INTO knowledge_chunks (chunk_id, tenant_id, source_doc, chunk_text, embedding)
                    VALUES (:cid, :tid, :doc, :txt, :emb)
                """),
                {
                    "cid": cid,
                    "tid": req.tenant_id,
                    "doc": c["source_doc"],
                    "txt": c["chunk_text"],
                    "emb": emb_json
                }
            )
        conn.commit()

    rag_engine.invalidate_tenant_rag_cache(req.tenant_id)

    return {
        "status": "success",
        "message": f"Successfully scraped & indexed {len(chunks)} knowledge chunks from {url}.",
        "chunks_indexed": len(chunks),
        "source_doc": doc_label,
        "sample_chunks": [c["chunk_text"][:120] + "..." for c in chunks[:3]]
    }


@tenant_guard.get("/knowledge")
def get_tenant_knowledge(tenant_id: str):
    """Retrieve indexed knowledge chunks for a tenant."""
    with sync_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT chunk_id, source_doc, chunk_text FROM knowledge_chunks WHERE tenant_id = :tid ORDER BY chunk_id ASC LIMIT 50"),
            {"tid": tenant_id}
        ).fetchall()
        chunks = [{"chunk_id": r[0], "source_doc": r[1], "chunk_text": r[2]} for r in rows]
    return {
        "status": "success",
        "tenant_id": tenant_id,
        "total_chunks": len(chunks),
        "chunks": chunks
    }



# -----------------------------------------------------------------------------
# 3. Dual-PDF Ingestion (PDF 2: Team & Department Directory)
# -----------------------------------------------------------------------------
@tenant_guard.post("/upload-team-pdf")
async def upload_team_pdf(
    tenant_id: str = Form(...),
    file: UploadFile = File(...)
):
    """
    Ingests Team & Escalation Directory PDF (PDF 2):
    Parses staff names, departments (Admissions, Billing, Support), and direct phone numbers
    using Groq LLM, saving them as structured routing rules in tenant_config.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported for Team Directory ingestion.")

    content = await file.read()
    if len(content) < 100:
        raise HTTPException(status_code=400, detail="Uploaded PDF file is empty or corrupted.")

    try:
        raw_text = extract_text_from_pdf(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to extract PDF text: {str(e)}")

    # Parse team structure via Groq LLM
    routes = parse_team_directory_with_llm(raw_text)

    # Persist in tenant_config
    import json
    primary_escalation = routes[0]["phone"] if routes else settings.DEFAULT_ESCALATION_NUMBER

    with sync_engine.connect() as conn:
        # Load existing config
        cfg = conn.execute(
            text("SELECT config_id, business_rules FROM tenant_config WHERE tenant_id = :tid ORDER BY version DESC LIMIT 1"),
            {"tid": tenant_id}
        ).fetchone()

        if cfg:
            cid = cfg[0]
            existing_rules = cfg[1] if isinstance(cfg[1], dict) else json.loads(cfg[1] or "{}")
            existing_rules["team_routing"] = routes
            conn.execute(
                text("""
                    UPDATE tenant_config 
                    SET business_rules = :rules, escalation_number = :esc
                    WHERE config_id = :cid
                """),
                {
                    "rules": json.dumps(existing_rules),
                    "esc": primary_escalation,
                    "cid": cid
                }
            )
        else:
            conn.execute(
                text("""
                    INSERT INTO tenant_config (config_id, tenant_id, version, persona, business_rules, escalation_number, is_active)
                    VALUES (:cid, :tid, 1, 'Helpful advisor', :rules, :esc, true)
                """),
                {
                    "cid": uuid.uuid4().hex,
                    "tid": tenant_id,
                    "rules": json.dumps({"team_routing": routes}),
                    "esc": primary_escalation
                }
            )
        conn.commit()

    _TENANT_CONFIG_CACHE.pop(tenant_id, None)

    return {
        "status": "success",
        "message": f"Successfully configured escalation routing for {len(routes)} department(s).",
        "team_routing": routes,
        "primary_escalation_number": primary_escalation
    }


# -----------------------------------------------------------------------------
# 4. Telephony Settings (Master Pool vs BYOC) & Numbers
# -----------------------------------------------------------------------------
@tenant_guard.post("/telephony-settings")
async def update_telephony_settings(req: TelephonySettingsRequest):
    """Updates tenant's telephony mode (Master Pool or BYOC Exotel credentials)."""
    with sync_engine.connect() as conn:
        conn.execute(
            text("""
                UPDATE tenants 
                SET telephony_mode = :mode,
                    exotel_account_sid = :sid,
                    exotel_api_key = :key,
                    exotel_api_token = :tok,
                    exotel_caller_id = :cid
                WHERE tenant_id = :tid
            """),
            {
                "mode": req.telephony_mode,
                "sid": req.exotel_account_sid,
                "key": req.exotel_api_key,
                "tok": req.exotel_api_token,
                "cid": req.exotel_caller_id,
                "tid": req.tenant_id
            }
        )
        conn.commit()

    _TENANT_CONFIG_CACHE.pop(req.tenant_id, None)

    return {
        "status": "success",
        "message": f"Telephony settings updated to '{req.telephony_mode}'.",
        "telephony_mode": req.telephony_mode
    }


@tenant_guard.get("/available-numbers")
async def get_available_numbers():
    """Lists all telephony numbers currently available in the platform inventory pool."""
    with sync_engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT number, vendor, sip_extension 
                FROM phone_number_pool 
                WHERE status = 'available' 
                ORDER BY vendor ASC, number ASC
            """)
        ).fetchall()
        return {
            "status": "success",
            "numbers": [
                {"number": r[0], "vendor": r[1], "sip_extension": r[2]}
                for r in rows
            ]
        }


@tenant_guard.get("/details/{tenant_id}")
async def get_tenant_details(tenant_id: str):
    """
    Returns complete profile, configuration, assigned phone numbers,
    and live statistics for a specific tenant workspace.
    """
    with sync_engine.connect() as conn:
        tenant = conn.execute(
            text("""
                SELECT tenant_id, business_name, email, plan, telephony_mode, status, created_at
                FROM tenants 
                WHERE tenant_id = :tid 
                   OR business_name ILIKE :tid
                LIMIT 1
            """),
            {"tid": tenant_id.strip()}
        ).fetchone()

        if not tenant:
            raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found.")

        actual_tid = tenant[0]

        # Fetch all assigned phone numbers, deduplicated by number
        num_rows = conn.execute(
            text("""
                SELECT number, MIN(vendor) as vendor
                FROM (
                    SELECT number, vendor FROM tenant_phone_numbers WHERE tenant_id = :tid AND is_active = true
                    UNION ALL
                    SELECT number, vendor FROM phone_number_pool WHERE assigned_tenant_id = :tid
                ) combined
                GROUP BY number
                ORDER BY number ASC
            """),
            {"tid": actual_tid}
        ).fetchall()

        # Standardize / canonicalize so variants like 09513886363 and 9513886363 collapse into +919513886363
        canonical_map = {}
        for r in num_rows:
            raw = r[0]
            clean = raw.replace(" ", "").replace("-", "")
            if clean.startswith("+"):
                can = clean
            elif len(clean) == 10 and clean.isdigit():
                can = "+91" + clean
            elif clean.startswith("0") and len(clean) == 11 and clean.isdigit():
                can = "+91" + clean[1:]
            else:
                can = clean
            if can not in canonical_map or raw.startswith("+"):
                canonical_map[can] = r[1] or "carrier"

        assigned_numbers = [
            {"number": num, "vendor": vend, "is_active": True}
            for num, vend in canonical_map.items()
        ]

        # Fetch all registered AI Counselors/Agents for this tenant
        ag_rows = conn.execute(
            text("""
                SELECT agent_id, name, role_title, persona, greeting, escalation_number, voice_id, language, is_default, is_active, created_at
                FROM agents
                WHERE (tenant_id = :tid OR tenant_id = :canon) AND is_active = true
                ORDER BY is_default DESC, created_at ASC
            """),
            {"tid": actual_tid, "canon": "cl_idigitalstudie_82d5" if "idigital" in actual_tid else actual_tid}
        ).fetchall()

        agents_list = [
            {
                "agent_id": a[0],
                "name": a[1],
                "role_title": a[2],
                "persona": a[3],
                "greeting": a[4],
                "escalation_number": a[5],
                "voice_id": a[6],
                "language": a[7],
                "is_default": bool(a[8]),
                "is_active": bool(a[9]),
                "created_at": a[10].isoformat() if a[10] else None
            }
            for a in ag_rows
        ]

        # Query assigned_agent_id on tenant_phone_numbers to bind to each DID
        did_agent_map = {}
        for r in conn.execute(
            text("SELECT number, assigned_agent_id FROM tenant_phone_numbers WHERE (tenant_id = :tid OR tenant_id = :canon)"),
            {"tid": actual_tid, "canon": "cl_idigitalstudie_82d5" if "idigital" in actual_tid else actual_tid}
        ).fetchall():
            if r[0]:
                did_agent_map[r[0]] = r[1]
                did_agent_map[r[0].lstrip("+")] = r[1]
                if not r[0].startswith("+"):
                    did_agent_map["+" + r[0]] = r[1]

        agents_by_id = {ag["agent_id"]: ag for ag in agents_list}

        for num_item in assigned_numbers:
            n_val = num_item["number"]
            assigned_aid = did_agent_map.get(n_val) or did_agent_map.get(n_val.lstrip("+"))
            if not assigned_aid and agents_list:
                assigned_aid = agents_list[0]["agent_id"]
            num_item["assigned_agent_id"] = assigned_aid
            if assigned_aid and assigned_aid in agents_by_id:
                num_item["assigned_agent_name"] = agents_by_id[assigned_aid]["name"]
                num_item["assigned_agent_role"] = agents_by_id[assigned_aid]["role_title"]
            else:
                num_item["assigned_agent_name"] = "Admissions Agent"
                num_item["assigned_agent_role"] = "Admissions"

        # Knowledge chunk count
        k_count = conn.execute(
            text("SELECT COUNT(*) FROM knowledge_chunks WHERE tenant_id = :tid"),
            {"tid": actual_tid}
        ).scalar() or 0

        # Calls count
        c_count = conn.execute(
            text("""
                SELECT COUNT(*) FROM calls 
                WHERE tenant_id = :tid 
                   OR tenant_id IN (
                       SELECT t2.tenant_id FROM tenants t2 
                       WHERE t2.business_name ILIKE (SELECT t1.business_name FROM tenants t1 WHERE t1.tenant_id = :tid LIMIT 1)
                   )
            """),
            {"tid": actual_tid}
        ).scalar() or 0

        # Config / persona / escalation
        cfg = conn.execute(
            text("SELECT persona, escalation_number, business_rules FROM tenant_config WHERE tenant_id = :tid ORDER BY version DESC LIMIT 1"),
            {"tid": actual_tid}
        ).fetchone()

        advisor_name = "Customer Advisor"
        persona = f"You are a helpful customer advisor for {tenant[1]}."
        escalation_number = settings.DEFAULT_ESCALATION_NUMBER

        if agents_list:
            advisor_name = agents_list[0]["name"]
            persona = agents_list[0]["persona"]
            escalation_number = agents_list[0]["escalation_number"] or escalation_number
        elif cfg:
            persona = cfg[0] or persona
            escalation_number = cfg[1] or escalation_number
            m = re.search(r"You are '([^']+)'|You are ([A-Z][a-z]+),", persona)
            if m:
                advisor_name = m.group(1) or m.group(2) or advisor_name

    primary_did = assigned_numbers[0]["number"] if assigned_numbers else None

    return {
        "status": "success",
        "tenant_id": actual_tid,
        "business_name": tenant[1],
        "email": tenant[2],
        "plan": tenant[3] or "growth",
        "telephony_mode": tenant[4] or "master_pool",
        "status": tenant[5] or "active",
        "assigned_numbers": assigned_numbers,
        "primary_did": primary_did,
        "advisor_name": advisor_name,
        "escalation_number": escalation_number,
        "persona": persona,
        "knowledge_chunks_count": k_count,
        "total_calls_count": c_count,
        "agents": agents_list,
        "active_agents_count": len(agents_list),
        "active_counselors_count": len(agents_list)
    }


@tenant_guard.post("/assign-number")
async def assign_virtual_number(req: AssignNumberRequest):
    """
    Binds a phone number (DID) to a specific tenant.
    Updates tenant_phone_numbers, marks phone_number_pool as assigned,
    and invalidates memory caches immediately.
    """
    clean_num = req.phone_number.replace(" ", "").replace("-", "").strip()
    if not clean_num:
        raise HTTPException(status_code=400, detail="Phone number is required.")

    if not clean_num.startswith("+") and len(clean_num) == 10 and clean_num.isdigit():
        clean_num = "+91" + clean_num

    vendor = req.vendor or ("rpdigital" if "731" in clean_num or len(clean_num) <= 6 else "exotel")

    with sync_engine.connect() as conn:
        # Verify tenant exists
        t_check = conn.execute(
            text("SELECT tenant_id, business_name FROM tenants WHERE tenant_id = :tid LIMIT 1"),
            {"tid": req.tenant_id}
        ).fetchone()
        if not t_check:
            raise HTTPException(status_code=404, detail=f"Tenant '{req.tenant_id}' not found.")

        # 1. Bind in tenant_phone_numbers
        conn.execute(
            text("""
                INSERT INTO tenant_phone_numbers (number, tenant_id, vendor, is_active)
                VALUES (:num, :tid, :vendor, true)
                ON CONFLICT (number) DO UPDATE SET tenant_id = :tid, vendor = :vendor, is_active = true
            """),
            {"num": clean_num, "tid": req.tenant_id, "vendor": vendor}
        )

        # 2. Update status in phone_number_pool
        conn.execute(
            text("""
                INSERT INTO phone_number_pool (number, vendor, status, assigned_tenant_id)
                VALUES (:num, :vendor, 'assigned', :tid)
                ON CONFLICT (number) DO UPDATE SET status = 'assigned', assigned_tenant_id = :tid
            """),
            {"num": clean_num, "vendor": vendor, "tid": req.tenant_id}
        )
        conn.commit()

    clear_did_cache(clean_num)
    _TENANT_CONFIG_CACHE.pop(req.tenant_id, None)

    return {
        "status": "success",
        "message": f"Virtual DID {clean_num} successfully assigned to '{t_check[1]}' ({req.tenant_id}).",
        "assigned_number": clean_num,
        "tenant_id": req.tenant_id
    }


@tenant_guard.post("/unassign-number")
async def unassign_virtual_number(req: UnassignNumberRequest):
    """
    Unbinds a phone number from a tenant and returns it to the available inventory pool.
    """
    clean_num = req.phone_number.replace(" ", "").replace("-", "").strip()
    if not clean_num.startswith("+") and len(clean_num) == 10 and clean_num.isdigit():
        clean_num = "+91" + clean_num

    with sync_engine.connect() as conn:
        # Deactivate / delete from tenant_phone_numbers
        conn.execute(
            text("DELETE FROM tenant_phone_numbers WHERE (number = :num OR number = :raw) AND tenant_id = :tid"),
            {"num": clean_num, "raw": clean_num.lstrip("+"), "tid": req.tenant_id}
        )
        # Release back to available pool
        conn.execute(
            text("UPDATE phone_number_pool SET status = 'available', assigned_tenant_id = NULL WHERE number = :num OR number = :raw"),
            {"num": clean_num, "raw": clean_num.lstrip("+")}
        )
        conn.commit()

    from app.core.tenant_service import clear_did_cache
    clear_did_cache(clean_num)
    _TENANT_CONFIG_CACHE.pop(req.tenant_id, None)

    return {
        "status": "success",
        "message": f"Phone number {clean_num} unassigned and returned to available inventory."
    }


# -----------------------------------------------------------------------------
# 4B. Multi-Agent & DID Routing Management (Department AI Agents)
# -----------------------------------------------------------------------------
@tenant_guard.get("/{tenant_id}/agents")
async def get_tenant_agents(tenant_id: str):
    """Retrieves all registered AI Agents for a tenant."""
    with sync_engine.connect() as conn:
        t_row = conn.execute(
            text("SELECT tenant_id, business_name FROM tenants WHERE tenant_id = :tid OR tenant_id = :canon LIMIT 1"),
            {"tid": tenant_id, "canon": "cl_idigitalstudie_82d5" if "idigital" in tenant_id else tenant_id}
        ).fetchone()
        actual_tid = t_row[0] if t_row else tenant_id

        rows = conn.execute(
            text("""
                SELECT a.agent_id, a.tenant_id, a.name, a.role_title, a.persona, a.greeting,
                       a.escalation_number, a.voice_id, a.language, a.is_default, a.is_active,
                       a.created_at,
                       (SELECT STRING_AGG(tpn.number, ', ') FROM tenant_phone_numbers tpn WHERE tpn.assigned_agent_id = a.agent_id) as assigned_dids
                FROM agents a
                WHERE (a.tenant_id = :tid OR a.tenant_id = :canon) AND a.is_active = true
                ORDER BY a.is_default DESC, a.created_at ASC
            """),
            {"tid": actual_tid, "canon": "cl_idigitalstudie_82d5" if "idigital" in actual_tid else actual_tid}
        ).fetchall()

        agents = [
            {
                "agent_id": r[0],
                "tenant_id": r[1],
                "name": r[2],
                "role_title": r[3],
                "persona": r[4],
                "greeting": r[5],
                "escalation_number": r[6],
                "voice_id": r[7],
                "language": r[8],
                "is_default": bool(r[9]),
                "is_active": bool(r[10]),
                "created_at": r[11].isoformat() if r[11] else None,
                "assigned_dids": [d.strip() for d in r[12].split(",")] if r[12] else []
            }
            for r in rows
        ]
        return {"status": "success", "tenant_id": actual_tid, "agents": agents, "count": len(agents)}


@tenant_guard.post("/{tenant_id}/agents")
async def create_tenant_agent(tenant_id: str, req: AgentCreateRequest):
    """Creates a new AI counselor/agent for the tenant."""
    agent_id = f"ag_{uuid.uuid4().hex[:10]}"
    with sync_engine.connect() as conn:
        t_row = conn.execute(
            text("SELECT tenant_id FROM tenants WHERE tenant_id = :tid OR tenant_id = :canon LIMIT 1"),
            {"tid": tenant_id, "canon": "cl_idigitalstudie_82d5" if "idigital" in tenant_id else tenant_id}
        ).fetchone()
        if not t_row:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        actual_tid = t_row[0]

        if req.is_default:
            conn.execute(
                text("UPDATE agents SET is_default = false WHERE tenant_id = :tid OR tenant_id = :canon"),
                {"tid": actual_tid, "canon": "cl_idigitalstudie_82d5" if "idigital" in actual_tid else actual_tid}
            )

        conn.execute(
            text("""
                INSERT INTO agents (agent_id, tenant_id, name, role_title, persona, greeting, escalation_number, voice_id, language, is_default, is_active)
                VALUES (:aid, :tid, :name, :role, :persona, :greeting, :esc, :voice, :lang, :is_def, true)
            """),
            {
                "aid": agent_id,
                "tid": actual_tid,
                "name": req.name.strip(),
                "role": req.role_title.strip() or "Counselor",
                "persona": req.persona.strip(),
                "greeting": req.greeting.strip(),
                "esc": req.escalation_number.strip() if req.escalation_number else None,
                "voice": req.voice_id or "default",
                "lang": req.language or "hi-IN",
                "is_def": bool(req.is_default)
            }
        )

        if req.assign_to_did:
            clean_did = req.assign_to_did.replace(" ", "").replace("-", "").strip()
            conn.execute(
                text("""
                    UPDATE tenant_phone_numbers 
                    SET assigned_agent_id = :aid 
                    WHERE (number = :num OR number = :with_plus OR number = :without_plus) 
                      AND (tenant_id = :tid OR tenant_id = :canon)
                """),
                {
                    "aid": agent_id,
                    "num": clean_did,
                    "with_plus": "+" + clean_did.lstrip("+"),
                    "without_plus": clean_did.lstrip("+"),
                    "tid": actual_tid,
                    "canon": "cl_idigitalstudie_82d5" if "idigital" in actual_tid else actual_tid
                }
            )
            clear_did_cache(clean_did)

        conn.commit()

    clear_agent_cache(agent_id)
    return {
        "status": "success",
        "message": f"Counselor '{req.name}' ({req.role_title}) successfully created.",
        "agent_id": agent_id,
        "name": req.name,
        "role_title": req.role_title
    }


@tenant_guard.put("/{tenant_id}/agents/{agent_id}")
async def update_tenant_agent(tenant_id: str, agent_id: str, req: AgentUpdateRequest):
    """Updates an existing AI counselor/agent."""
    with sync_engine.connect() as conn:
        t_row = conn.execute(
            text("SELECT tenant_id FROM tenants WHERE tenant_id = :tid OR tenant_id = :canon LIMIT 1"),
            {"tid": tenant_id, "canon": "cl_idigitalstudie_82d5" if "idigital" in tenant_id else tenant_id}
        ).fetchone()
        if not t_row:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        actual_tid = t_row[0]

        ag = conn.execute(
            text("SELECT agent_id FROM agents WHERE agent_id = :aid AND (tenant_id = :tid OR tenant_id = :canon) LIMIT 1"),
            {"aid": agent_id, "tid": actual_tid, "canon": "cl_idigitalstudie_82d5" if "idigital" in actual_tid else actual_tid}
        ).fetchone()
        if not ag:
            raise HTTPException(status_code=404, detail="Agent not found.")

        if req.is_default:
            conn.execute(
                text("UPDATE agents SET is_default = false WHERE tenant_id = :tid OR tenant_id = :canon"),
                {"tid": actual_tid, "canon": "cl_idigitalstudie_82d5" if "idigital" in actual_tid else actual_tid}
            )

        updates = []
        params = {"aid": agent_id}
        if req.name is not None:
            updates.append("name = :name")
            params["name"] = req.name.strip()
        if req.role_title is not None:
            updates.append("role_title = :role")
            params["role"] = req.role_title.strip()
        if req.persona is not None:
            updates.append("persona = :persona")
            params["persona"] = req.persona.strip()
        if req.greeting is not None:
            updates.append("greeting = :greeting")
            params["greeting"] = req.greeting.strip()
        if req.escalation_number is not None:
            updates.append("escalation_number = :esc")
            params["esc"] = req.escalation_number.strip() if req.escalation_number else None
        if req.voice_id is not None:
            updates.append("voice_id = :voice")
            params["voice"] = req.voice_id
        if req.language is not None:
            updates.append("language = :lang")
            params["lang"] = req.language
        if req.is_default is not None:
            updates.append("is_default = :is_def")
            params["is_def"] = bool(req.is_default)

        if updates:
            conn.execute(
                text(f"UPDATE agents SET {', '.join(updates)} WHERE agent_id = :aid"),
                params
            )
            conn.commit()

    clear_agent_cache(agent_id)
    return {"status": "success", "message": f"Agent '{agent_id}' updated successfully."}


@tenant_guard.delete("/{tenant_id}/agents/{agent_id}")
async def delete_tenant_agent(tenant_id: str, agent_id: str):
    """Deletes an agent (if not assigned to an active DID or only remaining agent)."""
    with sync_engine.connect() as conn:
        t_row = conn.execute(
            text("SELECT tenant_id FROM tenants WHERE tenant_id = :tid OR tenant_id = :canon LIMIT 1"),
            {"tid": tenant_id, "canon": "cl_idigitalstudie_82d5" if "idigital" in tenant_id else tenant_id}
        ).fetchone()
        actual_tid = t_row[0] if t_row else tenant_id

        # Check total agents
        total = conn.execute(
            text("SELECT COUNT(*) FROM agents WHERE (tenant_id = :tid OR tenant_id = :canon) AND is_active = true"),
            {"tid": actual_tid, "canon": "cl_idigitalstudie_82d5" if "idigital" in actual_tid else actual_tid}
        ).scalar() or 0
        if total <= 1:
            raise HTTPException(status_code=400, detail="Cannot delete the only remaining counselor for this business.")

        # Check assigned DIDs
        assigned = conn.execute(
            text("SELECT number FROM tenant_phone_numbers WHERE assigned_agent_id = :aid AND is_active = true"),
            {"aid": agent_id}
        ).fetchall()
        if assigned:
            dids_str = ", ".join(r[0] for r in assigned)
            raise HTTPException(status_code=400, detail=f"Cannot delete counselor while actively assigned to DID(s): {dids_str}. Reassign the DID first.")

        conn.execute(text("DELETE FROM agents WHERE agent_id = :aid"), {"aid": agent_id})
        conn.commit()

    clear_agent_cache(agent_id)
    return {"status": "success", "message": f"Counselor '{agent_id}' deleted successfully."}


@tenant_guard.post("/assign-agent-to-did")
async def assign_agent_to_did(req: AssignAgentToDIDRequest):
    """
    Assigns / Switches the active counselor for a specific DID number on demand.
    Flushes cache immediately for sub-millisecond instant switch.
    """
    clean_num = req.phone_number.replace(" ", "").replace("-", "").strip()
    with sync_engine.connect() as conn:
        t_row = conn.execute(
            text("SELECT tenant_id, business_name FROM tenants WHERE tenant_id = :tid OR tenant_id = :canon LIMIT 1"),
            {"tid": req.tenant_id, "canon": "cl_idigitalstudie_82d5" if "idigital" in req.tenant_id else req.tenant_id}
        ).fetchone()
        if not t_row:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        actual_tid = t_row[0]

        ag = conn.execute(
            text("SELECT agent_id, name, role_title, greeting FROM agents WHERE agent_id = :aid AND (tenant_id = :tid OR tenant_id = :canon) LIMIT 1"),
            {"aid": req.agent_id, "tid": actual_tid, "canon": "cl_idigitalstudie_82d5" if "idigital" in actual_tid else actual_tid}
        ).fetchone()
        if not ag:
            raise HTTPException(status_code=404, detail=f"Counselor '{req.agent_id}' not found for tenant '{actual_tid}'.")

        conn.execute(
            text("""
                UPDATE tenant_phone_numbers 
                SET assigned_agent_id = :aid 
                WHERE (number = :num OR number = :with_plus OR number = :without_plus) 
                  AND (tenant_id = :tid OR tenant_id = :canon)
            """),
            {
                "aid": req.agent_id,
                "num": clean_num,
                "with_plus": "+" + clean_num.lstrip("+"),
                "without_plus": clean_num.lstrip("+"),
                "tid": actual_tid,
                "canon": "cl_idigitalstudie_82d5" if "idigital" in actual_tid else actual_tid
            }
        )
        conn.commit()

    clear_did_cache(clean_num)
    clear_agent_cache(req.agent_id)

    return {
        "status": "success",
        "message": f"DID {clean_num} successfully switched to '{ag[1]}' ({ag[2]}). Inbound calls will now be answered by {ag[1]}!",
        "phone_number": clean_num,
        "agent_id": req.agent_id,
        "agent_name": ag[1],
        "role_title": ag[2],
        "greeting": ag[3]
    }


# -----------------------------------------------------------------------------
# 5. Interactive README & Integration Guide
# -----------------------------------------------------------------------------
@tenant_guard.get("/readme")
async def get_tenant_readme(tenant_id: str = "idigitalstudies"):
    """
    Returns custom, tailored integration documentation, call flow instructions,
    and webhook endpoints for this specific vendor.
    """
    base_url = settings.BASE_URL.rstrip("/")
    parsed = urlparse(base_url)
    ws_domain = parsed.netloc or "your-domain.com"
    ws_url = f"wss://{ws_domain}/exotel/stream"
    connect_url = f"{base_url}/exotel/voicebot-connect"

    with sync_engine.connect() as conn:
        tenant = conn.execute(
            text("SELECT business_name, telephony_mode, plan FROM tenants WHERE tenant_id = :tid"),
            {"tid": tenant_id}
        ).fetchone()
        numbers = conn.execute(
            text("SELECT number FROM tenant_phone_numbers WHERE tenant_id = :tid"),
            {"tid": tenant_id}
        ).fetchall()

    biz_name = tenant[0] if tenant else "Your Business"
    assigned_dids = [n[0] for n in numbers] if numbers else [settings.EXOTEL_CALLER_ID or "+917314624575"]

    return {
        "business_name": biz_name,
        "tenant_id": tenant_id,
        "assigned_dids": assigned_dids,
        "telephony_mode": tenant[1] if tenant else "master_pool",
        "endpoints": {
            "dynamic_connect_url": connect_url,
            "websocket_stream_url": ws_url,
            "transfer_callback_url": f"{base_url}/exotel/transfer-callback"
        },
        "quick_setup_steps": [
            {
                "step": 1,
                "title": "Provision Virtual Phone Number (DID)",
                "description": f"Your allocated phone number is {', '.join(assigned_dids)}. Calls to this number are automatically routed to your AI agent."
            },
            {
                "step": 2,
                "title": "Configure Exotel Voicebot Applet",
                "description": f"In your Exotel Call Flow Builder, add the 'Voicebot Applet' and set the Dynamic Connect URL to: {connect_url}"
            },
            {
                "step": 3,
                "title": "Stream Audio via WebSocket",
                "description": f"Set the full-duplex WebSocket stream URL to: {ws_url}"
            },
            {
                "step": 4,
                "title": "Upload Business Knowledge & Team PDFs",
                "description": "Upload your business catalog/FAQs (PDF 1) for automated RAG, and your Team Directory (PDF 2) for dynamic multi-department call transfers."
            }
        ]
    }


# -----------------------------------------------------------------------------
# 6. Leads & Customer Status Management (CRM Board)
# -----------------------------------------------------------------------------
@tenant_guard.get("/leads")
async def get_tenant_leads(tenant_id: str = "idigitalstudies"):
    """
    Retrieves all captured leads, status pipeline, and urgent callbacks for this vendor.
    Continuously auto-syncs inbound calls from callers into qualified sales pipeline leads.
    """
    with sync_engine.connect() as conn:
        # 1. Continuous auto-sync: find any inbound calls with phone numbers not yet in leads
        calls_to_sync = conn.execute(
            text("""
                SELECT call_id, tenant_id, caller_number, summary, primary_concern, caller_sentiment, start_time, duration, outcome_tag, action_required
                FROM calls
                WHERE (tenant_id = :tid OR tenant_id IN (
                    SELECT t2.tenant_id FROM tenants t2 
                    WHERE REPLACE(LOWER(t2.business_name), ' ', '') = (
                        SELECT REPLACE(LOWER(t1.business_name), ' ', '') FROM tenants t1 WHERE t1.tenant_id = :tid LIMIT 1
                    )
                ))
                AND caller_number IS NOT NULL 
                AND caller_number NOT IN ('', 'unknown', 'anonymous', 'web-simulator')
                AND call_id NOT IN (SELECT COALESCE(call_id, '') FROM leads WHERE call_id IS NOT NULL)
                ORDER BY start_time DESC
                LIMIT 100
            """),
            {"tid": tenant_id}
        ).fetchall()

        for c in calls_to_sync:
            c_cid, c_tid, c_phone, c_sum, c_conc, c_sent, c_time, c_dur, c_out, c_act = c
            sent_str = (c_sent or "").lower()
            conc_str = (c_conc or "").lower()
            sum_str = (c_sum or "").lower()
            act_str = (c_act or "").lower()
            out_str = (c_out or "").lower()
            dur = float(c_dur or 0.0)

            # Format caller phone (+91...)
            clean_phone = c_phone.lstrip("0") if c_phone.startswith("0") else c_phone
            if not clean_phone.startswith("+"):
                clean_phone = "+91" + clean_phone if len(clean_phone) == 10 else "+" + clean_phone

            # Name extraction if AI summary discovered real caller name
            caller_name = f"Caller ({clean_phone[-4:]})"
            if c_sum:
                name_match = re.search(r"caller[,\s]+([A-Z][a-z]+ [A-Z][a-z]+)", c_sum)
                if name_match:
                    caller_name = name_match.group(1)

            # Lead qualification status determination
            if "callback" in act_str or "transfer" in out_str or "callback" in out_str or "transfer" in conc_str:
                status = "callback"
            elif "brochure" in conc_str or "brochure" in sum_str or "fees" in conc_str or "interest" in sent_str or dur > 35:
                status = "hot"
            elif dur < 8 or "disconnect" in conc_str:
                status = "cold"
            else:
                status = "warm"

            reason = c_conc or (c_sum[:200] if c_sum else "Inbound customer inquiry.")
            if c_act and ("callback" in act_str or "follow" in act_str) and "URGENT" not in reason.upper():
                reason = f"{reason} | Action: {c_act}"

            conn.execute(
                text("""
                    INSERT INTO leads (lead_id, tenant_id, call_id, name, phone, reason, status, captured_at)
                    VALUES (:lid, :tid, :cid, :name, :phone, :reason, :status, :captured)
                """),
                {
                    "lid": uuid.uuid4().hex,
                    "tid": c_tid or tenant_id,
                    "cid": c_cid,
                    "name": caller_name,
                    "phone": clean_phone,
                    "reason": reason,
                    "status": status,
                    "captured": c_time or datetime.utcnow()
                }
            )

        if calls_to_sync:
            conn.commit()

        # 2. Retrieve all leads for this tenant / alias
        existing_rows = conn.execute(
            text("""
                SELECT lead_id, call_id, name, phone, reason, captured_at, COALESCE(status, 'hot') as status
                FROM leads 
                WHERE tenant_id = :tid 
                   OR tenant_id IN (
                       SELECT t2.tenant_id FROM tenants t2 
                       WHERE REPLACE(LOWER(t2.business_name), ' ', '') = (
                           SELECT REPLACE(LOWER(t1.business_name), ' ', '') FROM tenants t1 WHERE t1.tenant_id = :tid LIMIT 1
                       )
                   )
                ORDER BY captured_at DESC NULLS LAST
                LIMIT 100
            """),
            {"tid": tenant_id}
        ).fetchall()

    leads = []
    urgent_count = 0
    status_summary = {"hot": 0, "warm": 0, "cold": 0, "callback": 0, "converted": 0}

    for r in existing_rows:
        reason_text = r[4] or ""
        st = (r[6] or "hot").lower()
        if st not in status_summary:
            status_summary[st] = 0
        status_summary[st] += 1

        is_urgent = "URGENT" in reason_text.upper() or "FAILED" in reason_text.upper() or st == "callback"
        if is_urgent:
            urgent_count += 1

        leads.append({
            "lead_id": r[0],
            "call_id": r[1],
            "name": r[2] or "Inbound Lead",
            "phone": r[3] or "Unknown",
            "reason": reason_text,
            "status": st,
            "captured_at": r[5].strftime("%d %b %Y, %I:%M %p") if r[5] else "Recent",
            "is_urgent": is_urgent
        })

    return {
        "status": "success",
        "tenant_id": tenant_id,
        "total_leads": len(leads),
        "urgent_callbacks_count": urgent_count,
        "status_summary": status_summary,
        "leads": leads
    }


@tenant_guard.post("/leads/{lead_id}/status")
async def update_lead_status(lead_id: str, req: LeadStatusUpdateRequest):
    """Updates the qualification / sales status of a lead."""
    valid_statuses = ("hot", "warm", "cold", "callback", "converted")
    st = req.status.lower().strip()
    if st not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status '{req.status}'. Must be one of: {', '.join(valid_statuses)}")

    with sync_engine.connect() as conn:
        res = conn.execute(
            text("UPDATE leads SET status = :st WHERE lead_id = :lid"),
            {"st": st, "lid": lead_id}
        )
        conn.commit()
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Lead not found.")

        return {
            "status": "success",
            "message": f"Lead status updated to '{st}'.",
            "lead_id": lead_id,
            "new_status": st
        }


@tenant_guard.post("/leads")
async def create_tenant_lead(req: LeadCreateRequest):
    """Manually creates a lead in the tenant CRM."""
    if not req.phone or not req.phone.strip():
        raise HTTPException(status_code=400, detail="Phone number is required.")

    lid = uuid.uuid4().hex
    st = (req.status or "hot").lower().strip()
    with sync_engine.connect() as conn:
        conn.execute(
            text("""
                INSERT INTO leads (lead_id, tenant_id, name, phone, reason, status, captured_at)
                VALUES (:lid, :tid, :name, :phone, :reason, :st, :now)
            """),
            {
                "lid": lid,
                "tid": req.tenant_id,
                "name": req.name or "Inbound Lead",
                "phone": req.phone.strip(),
                "reason": req.reason or "Manual CRM lead entry",
                "st": st,
                "now": datetime.utcnow()
            }
        )
        conn.commit()

    return {
        "status": "success",
        "message": "Lead created successfully.",
        "lead_id": lid
    }



router.include_router(tenant_guard)
