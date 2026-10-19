"""
QwickCall Enterprise Authentication & Authorization Layer
Provides:
- Cryptographically secure PBKDF2-HMAC password hashing
- Signed JWT token creation and verification (HS256)
- Role-based FastAPI dependency guards for SuperAdmin, Resellers, and Tenants
- Tenant isolation and BOLA / IDOR protection

Identity is only ever established from a signed JWT or a stored API key.
Request-supplied tenant identifiers (headers, query, path, body) are never trusted
as authentication; they are checked against the caller's identity via
verify_tenant_access / resolve_tenant_scope.
"""
import hmac
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List

import jwt
from fastapi import Header, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import text

from app.config import settings
from app.db.session import sync_engine

security_scheme = HTTPBearer(auto_error=False)

# Demo / seed passwords that must never authenticate anyone
KNOWN_DEFAULT_PASSWORDS = frozenset({"admin123", "tenant123", "partner123", "password", "changeme"})
MIN_PASSWORD_LENGTH = 8

# Explicit tenant ID aliases for legacy pilot tenants whose data was written under two IDs
_TENANT_ALIAS_GROUPS = (
    ("cl_idigitalstudie_82d5", "idigitalstudies"),
    ("phonotel", "phono_tel"),
)


# -----------------------------------------------------------------------------
# 1. Password Hashing & Verification (PBKDF2-SHA256)
# -----------------------------------------------------------------------------
def hash_password(password: str) -> str:
    """Hashes a password with PBKDF2-HMAC-SHA256 and a random 16-byte salt."""
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100_000
    ).hex()
    return f"pbkdf2:sha256:100000${salt}${key}"


def verify_password(plain_password: str, stored_hash: Optional[str]) -> bool:
    """Verifies a plain password against a stored PBKDF2-SHA256 hash. Plaintext hashes are rejected."""
    if not plain_password or not stored_hash or not stored_hash.startswith("pbkdf2:sha256:"):
        return False
    try:
        parts = stored_hash.split("$")
        if len(parts) != 3:
            return False
        rounds = int(parts[0].split(":")[2])
        computed = hashlib.pbkdf2_hmac(
            "sha256",
            plain_password.encode("utf-8"),
            parts[1].encode("utf-8"),
            rounds
        ).hex()
        return hmac.compare_digest(computed, parts[2])
    except Exception:
        return False


def validate_new_password(password: Optional[str]) -> str:
    """Validates a password being set; raises 400 when it's missing, short, or a known default."""
    pwd = (password or "").strip()
    if len(pwd) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if pwd.lower() in KNOWN_DEFAULT_PASSWORDS:
        raise HTTPException(status_code=400, detail="This password is a known default and cannot be used.")
    return pwd


def check_login_password(plain_password: str, stored_hash: Optional[str]) -> bool:
    """Verifies user login password against stored hash."""
    return verify_password(plain_password, stored_hash)


# -----------------------------------------------------------------------------
# 2. JWT Token Generation & Verification
# -----------------------------------------------------------------------------
def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=settings.JWT_EXPIRE_MINUTES))
    to_encode.update({"exp": expire, "iat": now})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None


def _bearer_payload(credentials: Optional[HTTPAuthorizationCredentials]) -> Optional[Dict[str, Any]]:
    if credentials and credentials.credentials:
        return decode_access_token(credentials.credentials)
    return None


def _is_admin_payload(payload: Optional[Dict[str, Any]]) -> bool:
    return bool(payload) and payload.get("role") in ("superadmin", "admin")


# -----------------------------------------------------------------------------
# 3. Role-Based FastAPI Dependencies
# -----------------------------------------------------------------------------
def get_current_admin(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
) -> Dict[str, Any]:
    """Guards SuperAdmin endpoints. Requires a valid JWT with role 'superadmin' or 'admin'."""
    payload = _bearer_payload(credentials)
    if _is_admin_payload(payload):
        return {**payload, "role": "superadmin", "is_admin": True}
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="SuperAdmin authentication required. Please provide a valid Bearer token.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_reseller(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key")
) -> Dict[str, Any]:
    """Guards Reseller Partner endpoints. Requires a reseller JWT or an active reseller API key."""
    payload = _bearer_payload(credentials)
    if payload and payload.get("role") == "reseller" and payload.get("reseller_id"):
        return payload

    if x_api_key:
        with sync_engine.connect() as conn:
            row = conn.execute(
                text("SELECT reseller_id, company_name, email, status FROM resellers WHERE api_key = :k"),
                {"k": x_api_key.strip()}
            ).fetchone()
            if row and row[3] == "active":
                return {"reseller_id": row[0], "company_name": row[1], "email": row[2], "role": "reseller"}

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Reseller partner authentication required. Please provide a valid Bearer token or X-API-Key.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _authenticate_principal(
    credentials: Optional[HTTPAuthorizationCredentials],
    x_api_key: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Resolves the caller from a signed JWT or a stored tenant API key. Returns None if neither is valid."""
    payload = _bearer_payload(credentials)
    if payload:
        if _is_admin_payload(payload):
            return {"role": "superadmin", "is_admin": True}
        if payload.get("role") == "tenant" and payload.get("tenant_id"):
            return {"tenant_id": payload["tenant_id"], "role": "tenant", "is_admin": False}
        if payload.get("role") == "reseller" and payload.get("reseller_id"):
            return {"reseller_id": payload["reseller_id"], "role": "reseller", "is_admin": False}

    if x_api_key:
        with sync_engine.connect() as conn:
            row = conn.execute(
                text("SELECT tenant_id, business_name, status FROM tenants WHERE api_key = :k"),
                {"k": x_api_key.strip()}
            ).fetchone()
            if row and row[2] == "active":
                return {"tenant_id": row[0], "business_name": row[1], "role": "tenant", "is_admin": False}
    return None


def get_current_tenant_auth(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> Dict[str, Any]:
    """Guards tenant endpoints. Accepts a SuperAdmin/Reseller/Tenant JWT or a tenant API key."""
    principal = _authenticate_principal(credentials, x_api_key)
    if principal:
        return principal
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required. Please provide a valid Bearer token or X-API-Key.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_optional_tenant_auth(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> Optional[Dict[str, Any]]:
    """Optional auth helper: returns the authenticated principal or None."""
    return _authenticate_principal(credentials, x_api_key)


# -----------------------------------------------------------------------------
# 4. Tenant Isolation Helpers
# -----------------------------------------------------------------------------
def tenant_alias_ids(tenant_id: str) -> List[str]:
    """Returns the tenant ID plus any explicitly configured legacy aliases for it."""
    for group in _TENANT_ALIAS_GROUPS:
        if tenant_id in group:
            return list(group)
    return [tenant_id]


def tenant_canonical_alias(tenant_id: str) -> str:
    """Returns the paired legacy alias for a pilot tenant ID, or the ID itself (exact match only)."""
    for alias in tenant_alias_ids(tenant_id):
        if alias != tenant_id:
            return alias
    return tenant_id


def verify_tenant_access(requested_tenant_id: str, auth: Optional[Dict[str, Any]]) -> bool:
    """Enforces strict BOLA/IDOR isolation: ensures caller has rights to the requested tenant."""
    if not auth or not requested_tenant_id:
        return False
    if auth.get("is_admin") or auth.get("role") == "superadmin":
        return True
    caller_tenant = auth.get("tenant_id")
    if caller_tenant:
        return requested_tenant_id in tenant_alias_ids(caller_tenant)
    if auth.get("role") == "reseller" and auth.get("reseller_id"):
        with sync_engine.connect() as conn:
            row = conn.execute(
                text("SELECT reseller_id FROM tenants WHERE tenant_id = :tid"),
                {"tid": requested_tenant_id}
            ).fetchone()
            return bool(row and row[0] == auth["reseller_id"])
    return False


def resolve_tenant_scope(auth: Dict[str, Any], requested_tenant_id: Optional[str] = None) -> str:
    """
    Returns the tenant_id the caller may act on.
    Tenants are pinned to their own workspace; admins and resellers must name a tenant they can access.
    """
    requested = (requested_tenant_id or "").strip()
    if auth.get("role") == "tenant" and auth.get("tenant_id"):
        own = auth["tenant_id"]
        if requested and requested not in tenant_alias_ids(own):
            raise HTTPException(status_code=403, detail="Forbidden: You do not have permission to access this tenant.")
        return own
    if not requested:
        raise HTTPException(status_code=400, detail="tenant_id is required.")
    if not verify_tenant_access(requested, auth):
        raise HTTPException(status_code=403, detail="Forbidden: You do not have permission to access this tenant.")
    return requested


# -----------------------------------------------------------------------------
# 5. Telephony Webhook / Bridge Tokens
# -----------------------------------------------------------------------------
def verify_exotel_token(token: Optional[str]) -> bool:
    """Checks the shared ?token= on Exotel webhooks/streams. Open only when EXOTEL_WEBHOOK_TOKEN is unset (startup warns)."""
    expected = settings.EXOTEL_WEBHOOK_TOKEN
    if not expected:
        return True
    return bool(token) and hmac.compare_digest(token, expected)


def require_exotel_token(request: Request) -> None:
    """FastAPI dependency for Exotel HTTP webhooks."""
    if not verify_exotel_token(request.query_params.get("token")):
        raise HTTPException(status_code=403, detail="Invalid webhook token.")


def verify_internal_bridge_token(token: Optional[str]) -> bool:
    """Checks the token the in-process SIP client presents to the /sip/stream loopback WebSocket."""
    return bool(token) and hmac.compare_digest(token, settings.INTERNAL_BRIDGE_TOKEN)
