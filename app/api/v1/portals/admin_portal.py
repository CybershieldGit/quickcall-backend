"""
Platform Owner & SuperAdmin Router
Handles:
- Master SuperAdmin authentication
- Global Platform Financial Overview (Gross Revenue, Infra Costs, Net Profit, Airtime)
- Reseller Management (Set wholesale rates, top-up wallets, status)
- Direct Business Management (Retail rates, wallet balances)
- Global Telephony Pool Inventory (Add & allocate DIDs)
"""
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy import text

from app.config import settings
from app.db.session import sync_engine
from app.core.auth import hash_password, verify_password, create_access_token, get_current_admin

router = APIRouter(prefix="/api/v1/admin", tags=["SuperAdmin & Owner Portal"])


# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------
class AdminLoginRequest(BaseModel):
    email: str
    password: str

class ResellerCreateRequest(BaseModel):
    name: str
    company_name: str
    email: str
    password: str = "partner123"
    wholesale_rate_per_min: float = 3.00
    initial_wallet_balance: float = 1000.0

class ResellerRateUpdateRequest(BaseModel):
    wholesale_rate_per_min: float

class PasswordUpdateRequest(BaseModel):
    password: str

class WalletTopupRequest(BaseModel):
    amount: float
    notes: Optional[str] = "Admin top-up"

class DirectClientCreateRequest(BaseModel):
    business_name: str
    email: str
    rate_per_min: float = 5.00
    did_number: Optional[str] = None
    initial_wallet_balance: float = 500.0

class AdminTenantCreateRequest(BaseModel):
    business_name: str
    email: str
    password: Optional[str] = "tenant123"
    reseller_id: Optional[str] = None  # None or empty string for direct client
    plan: str = "growth"  # starter | growth | enterprise
    rate_per_min: float = 5.00
    initial_wallet_balance: float = 500.0
    did_number: Optional[str] = None
    advisor_name: Optional[str] = "Customer Advisor"
    escalation_number: Optional[str] = None

class TenantRateUpdateRequest(BaseModel):
    rate_per_min: float

class AddNumberToPoolRequest(BaseModel):
    number: str
    vendor: str = "rpdigital"  # rpdigital | exotel
    sip_extension: Optional[str] = None


# -----------------------------------------------------------------------------
# 1. Admin Authentication
# -----------------------------------------------------------------------------
@router.post("/login")
def admin_login(req: AdminLoginRequest):
    """Authenticate platform owner / superadmin."""
    # Check against settings master credentials or admin_users table
    if req.email == settings.ADMIN_EMAIL and (
        req.password == settings.ADMIN_PASSWORD or verify_password(req.password, settings.ADMIN_PASSWORD)
    ):
        token = create_access_token({"sub": "admin_root", "email": settings.ADMIN_EMAIL, "role": "superadmin"})
        return {
            "status": "success",
            "token": token,
            "user": {
                "name": "Platform Owner",
                "email": settings.ADMIN_EMAIL,
                "role": "superadmin"
            }
        }

    with sync_engine.connect() as conn:
        row = conn.execute(
            text("SELECT admin_id, name, email, password_hash, role FROM admin_users WHERE email = :email"),
            {"email": req.email}
        ).fetchone()

        if row and verify_password(req.password, row[3]):
            token = create_access_token({"sub": row[0], "email": row[2], "role": row[4]})
            return {
                "status": "success",
                "token": token,
                "user": {
                    "admin_id": row[0],
                    "name": row[1],
                    "email": row[2],
                    "role": row[4]
                }
            }

    raise HTTPException(status_code=401, detail="Invalid admin credentials.")

admin_guard = APIRouter(dependencies=[Depends(get_current_admin)])



# -----------------------------------------------------------------------------
# 2. Global Platform Financial Overview
# -----------------------------------------------------------------------------
@admin_guard.get("/overview")
def get_global_overview(admin_user: Dict[str, Any] = Depends(get_current_admin)):
    """
    Returns platform-wide executive metrics:
    Gross Airtime, Gross Revenue, Infrastructure Costs, Owner Net Margin, Reseller Profit Split.
    """
    with sync_engine.connect() as conn:
        # Calls and duration aggregation
        calls_row = conn.execute(text("""
            SELECT 
                COUNT(*) as total_calls,
                COALESCE(SUM(duration), 0) as total_duration_sec
            FROM calls
        """)).fetchone()

        total_calls = calls_row[0] or 0
        total_duration_sec = float(calls_row[1] or 0.0)
        total_minutes = round(total_duration_sec / 60.0, 2)

        # Base technical infra cost (e.g. ₹1.50/min)
        base_cost_rate = settings.PLATFORM_BASE_COST_PER_MIN
        total_infra_cost = round(total_minutes * base_cost_rate, 2)

        # Billing transactions aggregation
        billing_row = conn.execute(text("""
            SELECT 
                COALESCE(SUM(client_charged), 0) as total_client_charged,
                COALESCE(SUM(reseller_profit), 0) as total_reseller_profit,
                COALESCE(SUM(owner_net_profit), 0) as total_owner_net_profit
            FROM billing_transactions
        """)).fetchone()

        total_gross_revenue = round(float(billing_row[0] or (total_minutes * settings.DEFAULT_DIRECT_RETAIL_RATE_PER_MIN)), 2)
        total_reseller_profit = round(float(billing_row[1] or 0.0), 2)
        total_owner_profit = round(float(billing_row[2] or max(0.0, total_gross_revenue - total_infra_cost - total_reseller_profit)), 2)

        # Entity counts
        resellers_count = conn.execute(text("SELECT COUNT(*) FROM resellers WHERE status = 'active'")).scalar() or 0
        tenants_count = conn.execute(text("SELECT COUNT(*) FROM tenants WHERE status = 'active'")).scalar() or 0
        dids_count = conn.execute(text("SELECT COUNT(*) FROM phone_number_pool")).scalar() or 0
        available_dids_count = conn.execute(text("SELECT COUNT(*) FROM phone_number_pool WHERE status = 'available'")).scalar() or 0

        # Recent billing transactions
        recent_txs = conn.execute(text("""
            SELECT bt.transaction_id, bt.call_id, bt.tenant_id, t.business_name, 
                   bt.duration_seconds, bt.client_charged, bt.reseller_profit, bt.owner_net_profit, bt.timestamp
            FROM billing_transactions bt
            LEFT JOIN tenants t ON t.tenant_id = bt.tenant_id
            ORDER BY bt.timestamp DESC
            LIMIT 10
        """)).fetchall()

        transactions = [
            {
                "transaction_id": r[0],
                "call_id": r[1],
                "tenant_id": r[2],
                "business_name": r[3] or r[2],
                "duration_seconds": r[4],
                "client_charged": r[5],
                "reseller_profit": r[6],
                "owner_net_profit": r[7],
                "timestamp": str(r[8])
            }
            for r in recent_txs
        ]

        total_mins_int = int(total_duration_sec // 60)
        total_secs_int = int(total_duration_sec % 60)
        formatted_airtime = f"{total_mins_int}m {total_secs_int}s" if total_mins_int < 60 else f"{total_mins_int // 60}h {total_mins_int % 60}m"

        return {
            "status": "success",
            "metrics": {
                "total_calls": total_calls,
                "total_airtime_seconds": round(total_duration_sec, 1),
                "total_airtime_minutes": total_minutes,
                "total_airtime_formatted": formatted_airtime,
                "gross_revenue_inr": total_gross_revenue,
                "base_infra_cost_inr": total_infra_cost,
                "reseller_commission_inr": total_reseller_profit,
                "owner_net_profit_inr": total_owner_profit,
                "platform_net_margin_percent": round((total_owner_profit / total_gross_revenue * 100) if total_gross_revenue > 0 else 0.0, 1),
                "active_resellers_count": resellers_count,
                "active_tenants_count": tenants_count,
                "total_dids": dids_count,
                "available_dids": available_dids_count,
                "base_cost_per_min": base_cost_rate,
                "default_wholesale_rate": settings.DEFAULT_WHOLESALE_RATE_PER_MIN,
                "default_retail_rate": settings.DEFAULT_DIRECT_RETAIL_RATE_PER_MIN
            },
            "recent_transactions": transactions
        }


# -----------------------------------------------------------------------------
# 3. Reseller Management
# -----------------------------------------------------------------------------
@admin_guard.get("/resellers")
def list_resellers():
    """List all registered resellers with sub-client count, wholesale rate, and airtime."""
    with sync_engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT r.reseller_id, r.name, r.company_name, r.email, r.wholesale_rate_per_min,
                   r.wallet_balance, r.status, r.created_at,
                   COUNT(t.tenant_id) as sub_clients_count
            FROM resellers r
            LEFT JOIN tenants t ON t.reseller_id = r.reseller_id
            GROUP BY r.reseller_id, r.name, r.company_name, r.email, r.wholesale_rate_per_min, r.wallet_balance, r.status, r.created_at
            ORDER BY r.created_at DESC
        """)).fetchall()

        resellers = [
            {
                "reseller_id": r[0],
                "name": r[1],
                "company_name": r[2],
                "email": r[3],
                "wholesale_rate_per_min": r[4],
                "wallet_balance": round(r[5], 2),
                "status": r[6],
                "created_at": str(r[7]),
                "sub_clients_count": r[8]
            }
            for r in rows
        ]

        return {"status": "success", "resellers": resellers}


@admin_guard.post("/resellers")
def create_reseller(req: ResellerCreateRequest, admin_user: Dict[str, Any] = Depends(get_current_admin)):
    """Create a new reseller partner with assigned wholesale rate."""
    if req.wholesale_rate_per_min <= 0:
        raise HTTPException(status_code=400, detail="Wholesale rate per minute must be greater than zero.")
    if req.initial_wallet_balance < 0:
        raise HTTPException(status_code=400, detail="Initial wallet balance cannot be negative.")
    if not req.email or "@" not in req.email:
        raise HTTPException(status_code=400, detail="A valid email address is required.")

    clean_id = "res_" + "".join(filter(str.isalnum, req.company_name.lower()))[:16] or uuid.uuid4().hex[:8]
    api_key = f"qck_res_{uuid.uuid4().hex}"

    with sync_engine.connect() as conn:
        # Check existing
        existing = conn.execute(
            text("SELECT reseller_id FROM resellers WHERE email = :email OR reseller_id = :rid"),
            {"email": req.email, "rid": clean_id}
        ).fetchone()

        if existing:
            raise HTTPException(status_code=400, detail="Reseller with this email or company ID already exists.")

        conn.execute(text("""
            INSERT INTO resellers (reseller_id, name, company_name, email, password_hash, api_key, status, wholesale_rate_per_min, wallet_balance, branding)
            VALUES (:rid, :name, :company, :email, :pwd, :key, 'active', :rate, :bal, :brand)
        """), {
            "rid": clean_id,
            "name": req.name,
            "company": req.company_name,
            "email": req.email,
            "pwd": hash_password(req.password),
            "key": api_key,
            "rate": req.wholesale_rate_per_min,
            "bal": req.initial_wallet_balance,
            "brand": f'{{"portal_title": "{req.company_name} Voice Partner", "logo_url": "", "support_email": "{req.email}"}}'
        })
        conn.commit()

        return {
            "status": "success",
            "message": f"Reseller '{req.company_name}' successfully created.",
            "reseller": {
                "reseller_id": clean_id,
                "name": req.name,
                "company_name": req.company_name,
                "email": req.email,
                "wholesale_rate_per_min": req.wholesale_rate_per_min,
                "wallet_balance": req.initial_wallet_balance,
                "api_key": api_key
            }
        }


@admin_guard.post("/resellers/{reseller_id}/rate")
def update_reseller_rate(reseller_id: str, req: ResellerRateUpdateRequest):
    """Update wholesale rate per min charged to this reseller."""
    if req.wholesale_rate_per_min <= 0:
        raise HTTPException(status_code=400, detail="Wholesale rate must be greater than zero.")
    with sync_engine.connect() as conn:
        res = conn.execute(
            text("UPDATE resellers SET wholesale_rate_per_min = :rate WHERE reseller_id = :rid"),
            {"rate": req.wholesale_rate_per_min, "rid": reseller_id}
        )
        conn.commit()
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Reseller not found.")
        return {"status": "success", "message": f"Wholesale rate updated to ₹{req.wholesale_rate_per_min:.2f}/min."}


@admin_guard.post("/resellers/{reseller_id}/topup")
def topup_reseller_wallet(reseller_id: str, req: WalletTopupRequest, admin_user: Dict[str, Any] = Depends(get_current_admin)):
    """Credit funds/minutes to reseller wallet balance."""
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Top-up amount must be strictly greater than zero.")
    with sync_engine.connect() as conn:
        res = conn.execute(
            text("UPDATE resellers SET wallet_balance = wallet_balance + :amt WHERE reseller_id = :rid"),
            {"amt": req.amount, "rid": reseller_id}
        )
        conn.commit()
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Reseller not found.")
        
        new_bal = conn.execute(
            text("SELECT wallet_balance FROM resellers WHERE reseller_id = :rid"),
            {"rid": reseller_id}
        ).scalar() or 0.0

        return {"status": "success", "message": f"Added ₹{req.amount:.2f} to reseller wallet.", "new_balance": round(new_bal, 2)}


@admin_guard.post("/resellers/{reseller_id}/password")
def update_reseller_password(reseller_id: str, req: PasswordUpdateRequest, admin_user: Dict[str, Any] = Depends(get_current_admin)):
    """SuperAdmin updates or resets reseller partner login password."""
    if not req.password or len(req.password.strip()) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters long.")
    with sync_engine.connect() as conn:
        reseller = conn.execute(
            text("SELECT reseller_id, company_name FROM resellers WHERE reseller_id = :rid"),
            {"rid": reseller_id}
        ).fetchone()
        if not reseller:
            raise HTTPException(status_code=404, detail="Reseller not found.")
        conn.execute(
            text("UPDATE resellers SET password_hash = :pwd WHERE reseller_id = :rid"),
            {"pwd": hash_password(req.password.strip()), "rid": reseller_id}
        )
        conn.commit()
    return {"status": "success", "message": f"Password successfully updated for reseller '{reseller[1]}' ({reseller_id})."}


# -----------------------------------------------------------------------------
# 4. Master Tenants Management (Control & Monitoring over ALL Tenants)
# -----------------------------------------------------------------------------
@admin_guard.get("/tenants")
def list_all_tenants():
    """List all tenants across the platform (both direct and reseller-affiliated)."""
    with sync_engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT t.tenant_id, t.business_name, t.email, t.plan, t.telephony_mode,
                   t.rate_per_min, t.wallet_balance, t.status, t.created_at,
                   t.reseller_id, r.company_name as reseller_name,
                   (SELECT STRING_AGG(DISTINCT tpn.number, ', ') FROM tenant_phone_numbers tpn WHERE tpn.tenant_id = t.tenant_id AND tpn.is_active = true) as assigned_numbers,
                   (SELECT COUNT(*) FROM calls c WHERE c.tenant_id = t.tenant_id) as total_calls,
                   (SELECT COALESCE(SUM(c.duration), 0) FROM calls c WHERE c.tenant_id = t.tenant_id) as total_duration_sec
            FROM tenants t
            LEFT JOIN resellers r ON r.reseller_id = t.reseller_id
            ORDER BY t.created_at DESC
        """)).fetchall()

        tenants = []
        for r in rows:
            total_sec = float(r[13] or 0.0)
            tenants.append({
                "tenant_id": r[0],
                "business_name": r[1],
                "email": r[2],
                "plan": r[3],
                "telephony_mode": r[4],
                "rate_per_min": float(r[5] or 5.00),
                "wallet_balance": round(float(r[6] or 0.0), 2),
                "status": r[7],
                "created_at": str(r[8]),
                "reseller_id": r[9],
                "reseller_name": r[10] or "Direct Client (Platform)",
                "assigned_numbers": r[11] or "Unassigned",
                "total_calls": r[12] or 0,
                "total_airtime_minutes": round(total_sec / 60.0, 1)
            })

        return {"status": "success", "tenants": tenants}


@admin_guard.post("/tenants")
def admin_create_tenant(req: AdminTenantCreateRequest):
    """
    Onboards a new tenant directly from Admin Panel.
    Can be assigned to a Reseller Partner or direct to Platform.
    """
    if req.rate_per_min <= 0:
        raise HTTPException(status_code=400, detail="Retail rate must be greater than zero.")
    if req.initial_wallet_balance < 0:
        raise HTTPException(status_code=400, detail="Initial wallet balance cannot be negative.")
    if not req.email or "@" not in req.email:
        raise HTTPException(status_code=400, detail="A valid email address is required.")

    clean_slug = "".join(filter(str.isalnum, req.business_name.lower()))[:14] or uuid.uuid4().hex[:8]
    tenant_id = f"did_{clean_slug}_{uuid.uuid4().hex[:4]}"
    api_key = f"qck_live_{uuid.uuid4().hex}"
    reseller_id = req.reseller_id.strip() if (req.reseller_id and req.reseller_id.strip() and req.reseller_id.strip() != "direct") else None

    with sync_engine.connect() as conn:
        if reseller_id:
            r_check = conn.execute(
                text("SELECT reseller_id FROM resellers WHERE reseller_id = :rid"),
                {"rid": reseller_id}
            ).fetchone()
            if not r_check:
                raise HTTPException(status_code=404, detail=f"Reseller '{reseller_id}' not found.")

        # Allocate DID
        assigned_did = req.did_number
        if not assigned_did or not assigned_did.strip():
            pool_row = conn.execute(
                text("SELECT number FROM phone_number_pool WHERE status = 'available' LIMIT 1")
            ).fetchone()
            if pool_row:
                assigned_did = pool_row[0]
            else:
                assigned_did = f"+9173{uuid.uuid4().hex[:8].ljust(8, '0')[:8]}"

        conn.execute(text("""
            INSERT INTO tenants (tenant_id, business_name, email, password_hash, api_key, status, plan, telephony_mode, reseller_id, rate_per_min, wallet_balance)
            VALUES (:tid, :name, :email, :pwd, :key, 'active', :plan, 'master_pool', :rid, :rate, :bal)
        """), {
            "tid": tenant_id,
            "name": req.business_name,
            "email": req.email,
            "pwd": hash_password(req.password.strip() if req.password else "tenant123"),
            "key": api_key,
            "plan": req.plan,
            "rid": reseller_id,
            "rate": req.rate_per_min,
            "bal": req.initial_wallet_balance
        })

        # Bind phone number
        conn.execute(text("""
            INSERT INTO tenant_phone_numbers (number, tenant_id, vendor, is_active)
            VALUES (:num, :tid, 'rpdigital', true)
            ON CONFLICT (number) DO UPDATE SET tenant_id = EXCLUDED.tenant_id, is_active = true
        """), {"num": assigned_did, "tid": tenant_id})

        # Update pool status
        conn.execute(text("""
            UPDATE phone_number_pool SET status = 'assigned', assigned_tenant_id = :tid
            WHERE number = :num
        """), {"tid": tenant_id, "num": assigned_did})

        # Insert TenantConfig
        advisor = req.advisor_name or "Customer Advisor"
        esc_num = req.escalation_number or getattr(settings, "DEFAULT_ESCALATION_NUMBER", "+917022721098")
        persona = (
            f"You are '{advisor}', the AI customer advisor for {req.business_name}. "
            f"Greet callers warmly, answer questions accurately, and assist with their inquiries."
        )
        import json
        conn.execute(text("""
            INSERT INTO tenant_config (config_id, tenant_id, version, persona, business_rules, escalation_number, guardrail_topics, is_active)
            VALUES (:cfg_id, :tid, 1, :persona, :rules, :esc_num, '["price_haggling"]', true)
            ON CONFLICT (config_id) DO UPDATE SET persona = EXCLUDED.persona, escalation_number = EXCLUDED.escalation_number
        """), {
            "cfg_id": f"cfg_{tenant_id}",
            "tid": tenant_id,
            "persona": persona,
            "rules": json.dumps({"advisor_name": advisor}),
            "esc_num": esc_num
        })
        conn.commit()

        try:
            from app.core.tenant_service import _TENANT_CONFIG_CACHE, set_did_tenant_mapping
            _TENANT_CONFIG_CACHE.pop(tenant_id, None)
            if assigned_did:
                set_did_tenant_mapping(assigned_did, tenant_id)
        except Exception:
            pass

        return {
            "status": "success",
            "message": f"Tenant '{req.business_name}' onboarded successfully!",
            "tenant": {
                "tenant_id": tenant_id,
                "business_name": req.business_name,
                "email": req.email,
                "reseller_id": reseller_id,
                "assigned_did": assigned_did,
                "rate_per_min": req.rate_per_min,
                "wallet_balance": req.initial_wallet_balance
            }
        }


@admin_guard.post("/tenants/{tenant_id}/wallet")
def topup_tenant_wallet_admin(tenant_id: str, req: WalletTopupRequest, admin_user: Dict[str, Any] = Depends(get_current_admin)):
    """Credit funds/airtime to any tenant directly from Admin."""
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Top-up amount must be greater than zero.")
    with sync_engine.connect() as conn:
        res = conn.execute(
            text("UPDATE tenants SET wallet_balance = wallet_balance + :amt WHERE tenant_id = :tid"),
            {"amt": req.amount, "tid": tenant_id}
        )
        conn.commit()
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        new_bal = conn.execute(text("SELECT wallet_balance FROM tenants WHERE tenant_id = :tid"), {"tid": tenant_id}).scalar() or 0.0
        return {"status": "success", "message": f"Added ₹{req.amount:.2f} to tenant balance.", "new_balance": round(new_bal, 2)}


@admin_guard.post("/tenants/{tenant_id}/rate")
def update_tenant_rate_admin(tenant_id: str, req: TenantRateUpdateRequest):
    """Update retail rate charged to a tenant."""
    if req.rate_per_min <= 0:
        raise HTTPException(status_code=400, detail="Rate must be greater than zero.")
    with sync_engine.connect() as conn:
        res = conn.execute(
            text("UPDATE tenants SET rate_per_min = :rate WHERE tenant_id = :tid"),
            {"rate": req.rate_per_min, "tid": tenant_id}
        )
        conn.commit()
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        return {"status": "success", "message": f"Rate updated to ₹{req.rate_per_min:.2f}/min."}
 
 
@admin_guard.post("/tenants/{tenant_id}/password")
def update_tenant_password(tenant_id: str, req: PasswordUpdateRequest, admin_user: Dict[str, Any] = Depends(get_current_admin)):
    """SuperAdmin updates or resets tenant client login password."""
    if not req.password or len(req.password.strip()) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters long.")
    with sync_engine.connect() as conn:
        tenant = conn.execute(
            text("SELECT tenant_id, business_name FROM tenants WHERE tenant_id = :tid"),
            {"tid": tenant_id}
        ).fetchone()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant workspace not found.")
        conn.execute(
            text("UPDATE tenants SET password_hash = :pwd WHERE tenant_id = :tid"),
            {"pwd": hash_password(req.password.strip()), "tid": tenant_id}
        )
        conn.commit()
    return {"status": "success", "message": f"Password successfully updated for tenant '{tenant[1]}' ({tenant_id})."}


@admin_guard.get("/direct-clients")
def list_direct_clients():
    """List direct business tenants not under any reseller."""
    with sync_engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT t.tenant_id, t.business_name, t.email, t.plan, t.telephony_mode,
                   t.rate_per_min, t.wallet_balance, t.status, t.created_at,
                   (SELECT STRING_AGG(DISTINCT tpn.number, ', ') FROM tenant_phone_numbers tpn WHERE tpn.tenant_id = t.tenant_id AND tpn.is_active = true) as assigned_numbers
            FROM tenants t
            WHERE t.reseller_id IS NULL
            ORDER BY t.created_at DESC
        """)).fetchall()

        clients = [
            {
                "tenant_id": r[0],
                "business_name": r[1],
                "email": r[2],
                "plan": r[3],
                "telephony_mode": r[4],
                "rate_per_min": r[5] or 5.00,
                "wallet_balance": round(r[6] or 0.0, 2),
                "status": r[7],
                "created_at": str(r[8]),
                "assigned_numbers": r[9] or "None"
            }
            for r in rows
        ]
        return {"status": "success", "direct_clients": clients}


# -----------------------------------------------------------------------------
# 5. Global Telephony Pool Inventory (with /pool and /telephony-pool aliases)
# -----------------------------------------------------------------------------
@admin_guard.get("/pool")
@admin_guard.get("/telephony-pool")
def get_telephony_pool():
    """View all phone numbers in the platform inventory, their vendor, and assignment status."""
    with sync_engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT p.number, p.vendor, p.sip_extension, p.status, p.assigned_tenant_id,
                   t.business_name, p.created_at
            FROM phone_number_pool p
            LEFT JOIN tenants t ON t.tenant_id = p.assigned_tenant_id
            ORDER BY p.status ASC, p.number ASC
        """)).fetchall()

        numbers = [
            {
                "number": r[0],
                "vendor": r[1],
                "sip_extension": r[2],
                "status": r[3],  # available | assigned
                "assigned_tenant_id": r[4],
                "business_name": r[5] or "Unassigned",
                "created_at": str(r[6])
            }
            for r in rows
        ]
        return {"status": "success", "pool": numbers}


@admin_guard.post("/pool")
@admin_guard.post("/telephony-pool")
def add_number_to_pool(req: AddNumberToPoolRequest, admin_user: Dict[str, Any] = Depends(get_current_admin)):
    """Add a new Indian DID or SIP number to the platform inventory pool."""
    clean_num = req.number.replace(" ", "").replace("-", "").strip()
    with sync_engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO phone_number_pool (number, vendor, sip_extension, status)
            VALUES (:num, :vendor, :ext, 'available')
            ON CONFLICT (number) DO UPDATE SET vendor = EXCLUDED.vendor, sip_extension = EXCLUDED.sip_extension
        """), {
            "num": clean_num,
            "vendor": req.vendor,
            "ext": req.sip_extension
        })
        conn.commit()

        return {"status": "success", "message": f"Number {clean_num} added to available pool."}


router.include_router(admin_guard)
