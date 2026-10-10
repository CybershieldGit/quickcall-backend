"""
Tenant Configuration & DID Resolution Service
Loads tenant personas, business rules, multi-department team escalation routing,
and resolves inbound DIDs with Hybrid Telephony support.
"""
import os
import json
from typing import Dict, Any, Optional, Tuple
from sqlalchemy import text
from app.config import settings
from app.db.session import sync_engine

_TENANT_CONFIG_CACHE: Dict[str, Dict[str, Any]] = {}
_AGENT_CONFIG_CACHE: Dict[str, Dict[str, Any]] = {}
_DID_TENANT_CACHE: Dict[str, str] = {}
_DID_ROUTE_CACHE: Dict[str, Tuple[str, Optional[str]]] = {}
_TENANT_CREDENTIALS_CACHE: Dict[str, Dict[str, str]] = {}


def get_current_escalation_number() -> str:
    """Dynamically read the latest DEFAULT_ESCALATION_NUMBER directly from .env file to avoid stale caching."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("DEFAULT_ESCALATION_NUMBER=") and not line.startswith("#"):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            return val.replace(" ", "").replace("-", "")
        except Exception as e:
            print(f"[Dynamic Env Read Error] {e}")
    return (getattr(settings, "DEFAULT_ESCALATION_NUMBER", None) or "+919560732274").replace(" ", "").replace("-", "").strip()


def load_agent_config(agent_id: str, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Loads agent configuration (persona, greeting, name, role_title, escalation) from agents table."""
    if not agent_id:
        return None
    if agent_id in _AGENT_CONFIG_CACHE:
        return _AGENT_CONFIG_CACHE[agent_id]

    try:
        with sync_engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT a.agent_id, a.tenant_id, a.name, a.role_title, a.persona, a.greeting, 
                           a.escalation_number, a.voice_id, a.language, a.is_default, t.business_name
                    FROM agents a
                    LEFT JOIN tenants t ON t.tenant_id = a.tenant_id
                    WHERE a.agent_id = :aid AND a.is_active = true
                    LIMIT 1
                """),
                {"aid": agent_id}
            ).fetchone()

            if not row:
                return None

            tid = row[1]
            b_name = row[10] or ("iDigitalStudies" if "idigital" in tid else "Customer Care")
            esc_num = (row[6] or get_current_escalation_number() or "+919560732274").replace(" ", "").replace("-", "").strip()

            t_cfg = conn.execute(
                text("SELECT business_rules, guardrail_topics FROM tenant_config WHERE tenant_id = :tid ORDER BY version DESC LIMIT 1"),
                {"tid": tid}
            ).fetchone()
            rules = t_cfg[0] if t_cfg and isinstance(t_cfg[0], dict) else (json.loads(t_cfg[0]) if t_cfg and t_cfg[0] else {})
            guardrails = t_cfg[1] if t_cfg and t_cfg[1] else ["refund_negotiation", "price_bargaining", "dispute"]

            rules["agent_name"] = row[2]
            rules["advisor_name"] = row[2]
            rules["role_title"] = row[3]
            rules["greeting"] = row[5]

            cfg = {
                "agent_id": row[0],
                "tenant_id": tid,
                "advisor_name": row[2],
                "agent_name": row[2],
                "role_title": row[3],
                "persona": row[4],
                "greeting": row[5],
                "escalation_number": esc_num,
                "voice_id": row[7] or "default",
                "language": row[8] or "hi-IN",
                "is_default": bool(row[9]),
                "business_name": b_name,
                "business_rules": rules,
                "guardrail_topics": guardrails
            }
            _AGENT_CONFIG_CACHE[agent_id] = cfg
            return cfg
    except Exception as e:
        print(f"[Load Agent Config Error] {e}")
        return None


def load_tenant_config(tenant_id: str, agent_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Load tenant or specific agent persona, business rules, escalation number, and business_name."""
    if agent_id:
        ag_cfg = load_agent_config(agent_id, tenant_id)
        if ag_cfg:
            return ag_cfg

    tenant_id = normalize_tenant_id(tenant_id)
    cache_key = f"{tenant_id}:{agent_id or 'default'}"
    if cache_key in _TENANT_CONFIG_CACHE:
        return _TENANT_CONFIG_CACHE[cache_key]

    try:
        with sync_engine.connect() as conn:
            # First, check if there is an active default agent in agents table for this tenant
            ag_row = conn.execute(
                text("""
                    SELECT agent_id FROM agents 
                    WHERE (tenant_id = :tid OR tenant_id = :canon) AND is_active = true 
                    ORDER BY is_default DESC, created_at ASC 
                    LIMIT 1
                """),
                {"tid": tenant_id, "canon": "cl_idigitalstudie_82d5" if "idigital" in tenant_id else tenant_id}
            ).fetchone()
            if ag_row and ag_row[0]:
                ag_cfg = load_agent_config(ag_row[0], tenant_id)
                if ag_cfg:
                    _TENANT_CONFIG_CACHE[cache_key] = ag_cfg
                    return ag_cfg

            # Fallback to tenant_config table
            row = conn.execute(
                text("""
                    SELECT tc.persona, tc.business_rules, tc.escalation_number, tc.guardrail_topics, t.business_name
                    FROM tenant_config tc
                    LEFT JOIN tenants t ON t.tenant_id = tc.tenant_id
                    WHERE tc.tenant_id = :tid AND tc.is_active = true 
                    LIMIT 1
                """),
                {"tid": tenant_id}
            ).fetchone()

            if not row:
                t_row = conn.execute(
                    text("SELECT business_name FROM tenants WHERE tenant_id = :tid LIMIT 1"),
                    {"tid": tenant_id}
                ).fetchone()
                b_name = t_row[0] if t_row else ("iDigitalStudies" if tenant_id == "idigitalstudies" else "Customer Care")
                esc_num = get_current_escalation_number() or "+919560732274"
                cfg = {
                    "persona": f"You are a helpful and professional customer advisor for {b_name}.",
                    "business_name": b_name,
                    "business_rules": {},
                    "escalation_number": esc_num,
                    "guardrail_topics": ["refund_negotiation", "price_bargaining", "dispute"]
                }
                _TENANT_CONFIG_CACHE[cache_key] = cfg
                return cfg

            rules = row[1] if isinstance(row[1], dict) else json.loads(row[1] or "{}")
            esc_num = (row[2] or get_current_escalation_number() or "+919560732274").replace(" ", "").replace("-", "").strip()
            b_name = row[4] or ("iDigitalStudies" if tenant_id == "idigitalstudies" else "Customer Care")
            cfg = {
                "persona": row[0],
                "business_name": b_name,
                "business_rules": rules,
                "escalation_number": esc_num,
                "guardrail_topics": row[3] or []
            }
            _TENANT_CONFIG_CACHE[cache_key] = cfg
            return cfg
    except Exception as e:
        print(f"[Load Tenant Config Error] {e}")
        return None


def normalize_tenant_id(tenant_id: Optional[str]) -> str:
    """Normalizes tenant alias or returns canonical tenant UID."""
    if not tenant_id:
        return "idigitalstudies"
    tid = str(tenant_id).strip().lower()
    if tid in ("ids", "idigitalstudies", "idigital", "i_digital_studies"):
        return "idigitalstudies"
    if tid in ("phonotel", "phono_tel", "phono-tel"):
        return "phonotel"
    return tid


def clear_did_cache(number: Optional[str] = None):
    """Invalidates the dynamic DID -> tenant resolution cache."""
    global _DID_TENANT_CACHE, _DID_ROUTE_CACHE, _TENANT_CONFIG_CACHE, _AGENT_CONFIG_CACHE
    if number:
        cleaned = str(number).replace(" ", "").replace("-", "").strip()
        for k in [cleaned, cleaned.lstrip("+"), "+" + cleaned.lstrip("+")]:
            _DID_TENANT_CACHE.pop(k, None)
            _DID_ROUTE_CACHE.pop(k, None)
    else:
        _DID_TENANT_CACHE.clear()
        _DID_ROUTE_CACHE.clear()
        _TENANT_CONFIG_CACHE.clear()
        _AGENT_CONFIG_CACHE.clear()


def clear_agent_cache(agent_id: Optional[str] = None):
    """Invalidates agent configuration cache."""
    global _AGENT_CONFIG_CACHE, _TENANT_CONFIG_CACHE, _DID_ROUTE_CACHE
    if agent_id:
        _AGENT_CONFIG_CACHE.pop(agent_id, None)
    else:
        _AGENT_CONFIG_CACHE.clear()
    _TENANT_CONFIG_CACHE.clear()
    _DID_ROUTE_CACHE.clear()


def set_did_tenant_mapping(number: str, tenant_id: str, agent_id: Optional[str] = None):
    """Sets dynamic cache mapping for a DID number and its variants."""
    cleaned = number.replace(" ", "").replace("-", "").strip()
    _DID_TENANT_CACHE[cleaned] = tenant_id
    _DID_ROUTE_CACHE[cleaned] = (tenant_id, agent_id)
    without_plus = cleaned.lstrip("+")
    _DID_TENANT_CACHE[without_plus] = tenant_id
    _DID_ROUTE_CACHE[without_plus] = (tenant_id, agent_id)
    _DID_TENANT_CACHE["+" + without_plus] = tenant_id
    _DID_ROUTE_CACHE["+" + without_plus] = (tenant_id, agent_id)


def resolve_did_route(dialed_number: str) -> Tuple[str, Optional[str]]:
    """
    Resolves both (tenant_id, agent_id) from a dialed DID phone number.
    Returns (tenant_id, agent_id). If no specific agent is assigned to the DID,
    returns tenant's default agent_id.
    """
    if not dialed_number:
        return ("idigitalstudies", None)
    cleaned = dialed_number.replace(" ", "").replace("-", "").strip()

    if cleaned in _DID_ROUTE_CACHE:
        return _DID_ROUTE_CACHE[cleaned]

    try:
        with sync_engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT tenant_id, assigned_agent_id FROM tenant_phone_numbers 
                    WHERE (number = :num OR number = :with_plus OR number = :without_plus OR number LIKE :like_tail) 
                    AND is_active = true 
                    ORDER BY 
                        CASE WHEN number = :num OR number = :with_plus THEN 1 
                             WHEN number = :without_plus THEN 2 
                             ELSE 3 
                        END ASC
                    LIMIT 1
                """),
                {
                    "num": cleaned,
                    "with_plus": "+" + cleaned if not cleaned.startswith("+") else cleaned,
                    "without_plus": cleaned.lstrip("+"),
                    "like_tail": f"%{cleaned[-10:]}" if len(cleaned) >= 10 else f"%{cleaned}"
                }
            ).fetchone()

            if row and row[0]:
                tid = row[0]
                aid = row[1]
                if not aid:
                    def_ag = conn.execute(
                        text("SELECT agent_id FROM agents WHERE tenant_id = :tid AND is_default = true AND is_active = true LIMIT 1"),
                        {"tid": tid}
                    ).fetchone()
                    if def_ag:
                        aid = def_ag[0]
                res = (tid, aid)
                _DID_ROUTE_CACHE[cleaned] = res
                _DID_TENANT_CACHE[cleaned] = tid
                return res

            # Fallback check on phone_number_pool
            pool_row = conn.execute(
                text("""
                    SELECT assigned_tenant_id FROM phone_number_pool
                    WHERE (number = :num OR number = :with_plus OR number = :without_plus OR sip_extension = :num)
                    AND assigned_tenant_id IS NOT NULL
                    LIMIT 1
                """),
                {
                    "num": cleaned,
                    "with_plus": "+" + cleaned if not cleaned.startswith("+") else cleaned,
                    "without_plus": cleaned.lstrip("+")
                }
            ).fetchone()

            if pool_row and pool_row[0]:
                tid = pool_row[0]
                def_ag = conn.execute(
                    text("SELECT agent_id FROM agents WHERE tenant_id = :tid AND is_default = true AND is_active = true LIMIT 1"),
                    {"tid": tid}
                ).fetchone()
                aid = def_ag[0] if def_ag else None
                res = (tid, aid)
                _DID_ROUTE_CACHE[cleaned] = res
                _DID_TENANT_CACHE[cleaned] = tid
                return res

            tid = resolve_tenant_from_did(cleaned)
            def_ag = conn.execute(
                text("SELECT agent_id FROM agents WHERE (tenant_id = :tid OR tenant_id = :canon) AND is_default = true AND is_active = true LIMIT 1"),
                {"tid": tid, "canon": "cl_idigitalstudie_82d5" if "idigital" in tid else tid}
            ).fetchone()
            aid = def_ag[0] if def_ag else None
            res = (tid, aid)
            _DID_ROUTE_CACHE[cleaned] = res
            return res
    except Exception as e:
        print(f"[Resolve DID Route Error] {e}")
        return ("idigitalstudies", None)


def resolve_tenant_from_did(dialed_number: str) -> str:
    """Resolve tenant_id from a dialed DID phone number."""
    tid, _ = resolve_did_route(dialed_number)
    return tid


def get_tenant_escalation_target(tenant_id: str, caller_utterance: str = "") -> Tuple[str, str]:
    """
    Determines the exact team member phone number and department name
    by matching the caller's query against the tenant's team_routing rules.
    """
    cfg = load_tenant_config(tenant_id)
    default_phone = cfg.get("escalation_number", settings.DEFAULT_ESCALATION_NUMBER) if cfg else settings.DEFAULT_ESCALATION_NUMBER
    default_dept = "Admissions & Inquiries"

    if not cfg:
        return (default_phone, default_dept)

    team_routes = cfg.get("business_rules", {}).get("team_routing", [])
    if not team_routes or not isinstance(team_routes, list):
        return (default_phone, default_dept)

    clean_speech = caller_utterance.lower()

    # Search for matching keywords in team routing rules
    for member in team_routes:
        keywords = member.get("keywords", [])
        dept = member.get("department", "Support")
        phone = member.get("phone", "")
        if phone:
            # Check keywords or department name match
            if any(k.lower() in clean_speech for k in keywords) or dept.lower() in clean_speech:
                return (phone, dept)

    # Fallback to the first team member or default
    first_member = team_routes[0]
    return (first_member.get("phone", default_phone), first_member.get("department", default_dept))


def get_tenant_exotel_credentials(tenant_id: str) -> Dict[str, str]:
    """
    Retrieves telephony credentials for the tenant.
    Supports Hybrid Telephony:
    - In 'byoc' mode: uses the enterprise's custom Exotel Account SID & API keys.
    - In 'master_pool' mode: uses the platform's master Exotel account.
    """
    if tenant_id in _TENANT_CREDENTIALS_CACHE:
        return _TENANT_CREDENTIALS_CACHE[tenant_id]

    try:
        with sync_engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT telephony_mode, exotel_account_sid, exotel_api_key, exotel_api_token, exotel_caller_id
                    FROM tenants WHERE tenant_id = :tid LIMIT 1
                """),
                {"tid": tenant_id}
            ).fetchone()

        if row and row[0] == "byoc" and row[1] and row[2] and row[3]:
            creds = {
                "account_sid": row[1],
                "api_key": row[2],
                "api_token": row[3],
                "caller_id": row[4] or settings.EXOTEL_CALLER_ID,
                "subdomain": settings.EXOTEL_SUBDOMAIN
            }
        else:
            # Master pool credentials
            creds = {
                "account_sid": settings.EXOTEL_ACCOUNT_SID,
                "api_key": settings.EXOTEL_API_KEY,
                "api_token": settings.EXOTEL_API_TOKEN,
                "caller_id": settings.EXOTEL_CALLER_ID,
                "subdomain": settings.EXOTEL_SUBDOMAIN
            }
        _TENANT_CREDENTIALS_CACHE[tenant_id] = creds
        return creds
    except Exception as e:
        print(f"[Tenant Credentials Error] {e}")
        return {
            "account_sid": settings.EXOTEL_ACCOUNT_SID,
            "api_key": settings.EXOTEL_API_KEY,
            "api_token": settings.EXOTEL_API_TOKEN,
            "caller_id": settings.EXOTEL_CALLER_ID,
            "subdomain": settings.EXOTEL_SUBDOMAIN
        }


def check_tenant_wallet_balance(tenant_id: str, min_required: float = 15.0) -> Tuple[bool, float, str]:
    """
    Checks if tenant has sufficient prepaid wallet balance to make/receive calls.
    Returns (is_sufficient, current_balance, message).
    """
    tid = normalize_tenant_id(tenant_id)
    try:
        with sync_engine.connect() as conn:
            row = conn.execute(
                text("SELECT wallet_balance, status FROM tenants WHERE tenant_id = :tid LIMIT 1"),
                {"tid": tid}
            ).fetchone()
            if not row:
                return True, 500.0, "Tenant record not found, demo credit assumed."
            balance = float(row[0] or 0.0)
            status = str(row[1] or "active").lower()
            if status != "active":
                return False, balance, f"Tenant account '{tid}' is currently {status}."
            if balance < min_required:
                return False, balance, f"Insufficient wallet balance (₹{balance:.2f}). Minimum ₹{min_required:.2f} required."
            return True, balance, "OK"
    except Exception as e:
        print(f"[Check Wallet Error] {e}")
        return True, 500.0, "Bypass on error"

