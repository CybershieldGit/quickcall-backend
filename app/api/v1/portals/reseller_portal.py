"""
Reseller & Agency Partner Router
Handles:
- Reseller login & authentication
- Reseller Financials (Wallet balance, wholesale rate, client airtime, net margin profit)
- Sub-Client Business Management (Onboard clients, set custom retail rates, live margin calculation)
- Sub-Client DID Allocation from Master Pool
- Sub-Client Wallet Top-up
- Whitelabel Portal Customization (Brand Name, Logo, Support Email)
"""
import uuid
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy import text

from app.config import settings
from app.db.session import sync_engine
from app.core.auth import verify_password, hash_password, create_access_token, get_current_reseller

router = APIRouter(prefix="/api/v1/reseller", tags=["Reseller Partner Portal"])


# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------
class ResellerLoginRequest(BaseModel):
    email: str
    password: str

class SubClientCreateRequest(BaseModel):
    reseller_id: str = "partner_apex"
    business_name: str
    email: str
    password: Optional[str] = "tenant123"
    retail_rate_per_min: float = 5.00
    plan: str = "starter"  # starter | growth | enterprise
    initial_wallet_balance: float = 500.0
    did_number: Optional[str] = None  # Optional explicit number; otherwise allocated from pool
    advisor_name: Optional[str] = "Customer Advisor"
    escalation_number: Optional[str] = None
    knowledge_text: Optional[str] = None

class SubClientRateRequest(BaseModel):
    retail_rate_per_min: float

class SubClientTopupRequest(BaseModel):
    amount: float
    notes: Optional[str] = "Partner credit top-up"

class ResellerBrandingRequest(BaseModel):
    reseller_id: str
    portal_title: str
    logo_url: Optional[str] = ""
    support_email: Optional[str] = ""
    accent_color: Optional[str] = "#6366F1"


# -----------------------------------------------------------------------------
# 1. Reseller Authentication
# -----------------------------------------------------------------------------
@router.post("/login")
def reseller_login(req: ResellerLoginRequest):
    """Authenticate reseller / agency partner."""
    with sync_engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT reseller_id, name, company_name, email, password_hash, api_key,
                       status, wholesale_rate_per_min, wallet_balance, branding
                FROM resellers
                WHERE email = :email
            """),
            {"email": req.email}
        ).fetchone()

        if not row:
            raise HTTPException(status_code=401, detail="Reseller account not found.")

        # In production use bcrypt; here check password or API key
        if not (verify_password(req.password, row[4]) or (row[5] and req.password == row[5])):
            raise HTTPException(status_code=401, detail="Invalid password.")

        if row[6] != "active":
            raise HTTPException(status_code=403, detail="Reseller account is suspended.")

        branding = row[9] if isinstance(row[9], dict) else {}

        return {
            "status": "success",
            "token": create_access_token({"sub": row[0], "reseller_id": row[0], "email": row[3], "role": "reseller"}),
            "reseller": {
                "reseller_id": row[0],
                "name": row[1],
                "company_name": row[2],
                "email": row[3],
                "api_key": row[5],
                "wholesale_rate_per_min": row[7],
                "wallet_balance": round(row[8], 2),
                "branding": branding
            }
        }


# -----------------------------------------------------------------------------

reseller_guard = APIRouter(dependencies=[Depends(get_current_reseller)])

# 2. Reseller Overview & Margin Economics
# -----------------------------------------------------------------------------
@reseller_guard.get("/overview")
def get_reseller_overview(reseller_id: str):
    """
    Returns reseller's financial metrics:
    Wholesale rate, Wallet balance, Sub-clients count, Client Airtime, Gross Billings, Wholesale Cost, Net Profit Earned.
    """
    with sync_engine.connect() as conn:
        reseller = conn.execute(
            text("SELECT reseller_id, name, company_name, wholesale_rate_per_min, wallet_balance, branding FROM resellers WHERE reseller_id = :rid"),
            {"rid": reseller_id}
        ).fetchone()

        if not reseller:
            raise HTTPException(status_code=404, detail="Reseller not found.")

        wholesale_rate = float(reseller[3] or settings.DEFAULT_WHOLESALE_RATE_PER_MIN)
        wallet_balance = float(reseller[4] or 0.0)

        # Query sub-clients stats
        clients_summary = conn.execute(
            text("""
                SELECT 
                    COUNT(t.tenant_id) as total_clients,
                    COALESCE(SUM(t.wallet_balance), 0) as clients_total_balance
                FROM tenants t
                WHERE t.reseller_id = :rid AND t.status = 'active'
            """),
            {"rid": reseller_id}
        ).fetchone()

        total_clients = clients_summary[0] or 0
        clients_total_balance = round(float(clients_summary[1] or 0.0), 2)

        # Query billing transactions for this reseller's clients
        billing_row = conn.execute(
            text("""
                SELECT 
                    COALESCE(SUM(bt.billable_minutes), 0) as total_mins,
                    COALESCE(SUM(bt.client_charged), 0) as gross_revenue,
                    COALESCE(SUM(bt.reseller_cost), 0) as wholesale_cost,
                    COALESCE(SUM(bt.reseller_profit), 0) as net_profit
                FROM billing_transactions bt
                WHERE bt.reseller_id = :rid
            """),
            {"rid": reseller_id}
        ).fetchone()

        total_mins = round(float(billing_row[0] or 0.0), 2)
        gross_revenue = round(float(billing_row[1] or 0.0), 2)
        wholesale_cost = round(float(billing_row[2] or 0.0), 2)
        net_profit = round(float(billing_row[3] or (gross_revenue - wholesale_cost)), 2)

        # Average margin per minute across all client plans
        avg_rate_row = conn.execute(
            text("SELECT AVG(rate_per_min) FROM tenants WHERE reseller_id = :rid"),
            {"rid": reseller_id}
        ).scalar()
        avg_retail_rate = float(avg_rate_row or 5.00)
        avg_margin_per_min = round(max(0.0, avg_retail_rate - wholesale_rate), 2)

        return {
            "status": "success",
            "reseller_id": reseller_id,
            "company_name": reseller[2],
            "metrics": {
                "wholesale_rate_per_min": wholesale_rate,
                "reseller_wallet_balance": round(wallet_balance, 2),
                "total_sub_clients": total_clients,
                "clients_total_wallet_balance": clients_total_balance,
                "total_client_minutes": total_mins,
                "gross_revenue_inr": gross_revenue,
                "wholesale_cost_inr": wholesale_cost,
                "net_profit_earned_inr": net_profit,
                "avg_retail_rate_per_min": avg_retail_rate,
                "avg_margin_per_min": avg_margin_per_min,
                "profit_margin_percent": round((net_profit / gross_revenue * 100) if gross_revenue > 0 else 0.0, 1)
            },
            "branding": reseller[5] if isinstance(reseller[5], dict) else {}
        }


# -----------------------------------------------------------------------------
# 3. Sub-Client Management
# -----------------------------------------------------------------------------
@reseller_guard.get("/clients")
def list_sub_clients(reseller_id: str):
    """List all business tenants managed by this reseller partner."""
    with sync_engine.connect() as conn:
        # Get reseller wholesale rate
        wholesale_rate = conn.execute(
            text("SELECT wholesale_rate_per_min FROM resellers WHERE reseller_id = :rid"),
            {"rid": reseller_id}
        ).scalar() or 3.00

        rows = conn.execute(text("""
            SELECT t.tenant_id, t.business_name, t.email, t.plan, t.telephony_mode,
                   t.rate_per_min, t.wallet_balance, t.status, t.created_at,
                   (SELECT STRING_AGG(DISTINCT tpn.number, ', ') FROM tenant_phone_numbers tpn WHERE tpn.tenant_id = t.tenant_id AND tpn.is_active = true) as assigned_numbers,
                   (SELECT COUNT(*) FROM calls c WHERE c.tenant_id = t.tenant_id) as total_calls,
                   (SELECT COALESCE(SUM(c.duration), 0) FROM calls c WHERE c.tenant_id = t.tenant_id) as total_duration_sec
            FROM tenants t
            WHERE t.reseller_id = :rid
            ORDER BY t.created_at DESC
        """), {"rid": reseller_id}).fetchall()

        clients = []
        for r in rows:
            retail_rate = float(r[5] or 5.00)
            margin_per_min = round(max(0.0, retail_rate - float(wholesale_rate)), 2)
            total_sec = float(r[11] or 0.0)
            mins = round(total_sec / 60.0, 1)
            clients.append({
                "tenant_id": r[0],
                "business_name": r[1],
                "email": r[2],
                "plan": r[3],
                "telephony_mode": r[4],
                "retail_rate_per_min": retail_rate,
                "wholesale_rate_per_min": float(wholesale_rate),
                "margin_per_min": margin_per_min,
                "wallet_balance": round(float(r[6] or 0.0), 2),
                "status": r[7],
                "created_at": str(r[8]),
                "assigned_numbers": r[9] or "Unassigned",
                "total_calls": r[10],
                "total_airtime_minutes": mins
            })

        return {"status": "success", "clients": clients}


@reseller_guard.post("/clients")
def create_sub_client(req: SubClientCreateRequest):
    """
    Onboards a new client business under this reseller.
    Automatically assigns an available DID from phone_number_pool.
    """
    if req.retail_rate_per_min <= 0:
        raise HTTPException(status_code=400, detail="Retail rate per minute must be greater than zero.")
    if req.initial_wallet_balance < 0:
        raise HTTPException(status_code=400, detail="Initial wallet balance cannot be negative.")
    if not req.email or "@" not in req.email:
        raise HTTPException(status_code=400, detail="A valid email address is required.")

    clean_slug = "".join(filter(str.isalnum, req.business_name.lower()))[:14] or uuid.uuid4().hex[:8]
    tenant_id = f"cl_{clean_slug}_{uuid.uuid4().hex[:4]}"
    api_key = f"qck_cl_{uuid.uuid4().hex}"

    with sync_engine.connect() as conn:
        # Check reseller exists
        reseller = conn.execute(
            text("SELECT reseller_id, wholesale_rate_per_min FROM resellers WHERE reseller_id = :rid"),
            {"rid": req.reseller_id}
        ).fetchone()

        if not reseller:
            raise HTTPException(status_code=404, detail="Reseller not found.")

        # Allocate DID number
        assigned_did = req.did_number
        if not assigned_did or not assigned_did.strip():
            pool_row = conn.execute(
                text("SELECT number FROM phone_number_pool WHERE status = 'available' LIMIT 1")
            ).fetchone()
            if pool_row:
                assigned_did = pool_row[0]
            else:
                # Dynamic virtual DID fallback
                assigned_did = f"+9173{uuid.uuid4().hex[:8].ljust(8, '0')[:8]}"

        # Insert Tenant
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
            "rid": req.reseller_id,
            "rate": req.retail_rate_per_min,
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

        # Custom or default persona & configuration
        advisor = req.advisor_name or "Customer Advisor"
        esc_num = req.escalation_number or getattr(settings, "DEFAULT_ESCALATION_NUMBER", "+917022721098")
        persona = (
            f"You are '{advisor}', the professional and knowledgeable AI customer advisor for {req.business_name}. "
            f"Greet callers warmly, answer questions accurately based on company documents, and assist with their inquiries. "
            f"Keep responses conversational, warm, and concise (1-2 sentences)."
        )
        import json
        conn.execute(text("""
            INSERT INTO tenant_config (config_id, tenant_id, version, persona, business_rules, escalation_number, guardrail_topics, is_active)
            VALUES (:cfg_id, :tid, 1, :persona, :rules, :esc_num, '["price_haggling", "dispute"]', true)
            ON CONFLICT (config_id) DO UPDATE SET persona = EXCLUDED.persona, escalation_number = EXCLUDED.escalation_number
        """), {
            "cfg_id": f"cfg_{tenant_id}",
            "tid": tenant_id,
            "persona": persona,
            "rules": json.dumps({"advisor_name": advisor, "agent_name": advisor}),
            "esc_num": esc_num
        })

        if req.knowledge_text and req.knowledge_text.strip():
            conn.execute(text("""
                INSERT INTO knowledge_chunks (chunk_id, tenant_id, source_doc, chunk_text)
                VALUES (:cid, :tid, 'onboarding_facts', :text)
            """), {
                "cid": f"chk_{uuid.uuid4().hex[:8]}",
                "tid": tenant_id,
                "text": req.knowledge_text.strip()
            })

        conn.commit()

        # Invalidate in-memory caches
        try:
            from app.core.tenant_service import _TENANT_CONFIG_CACHE, set_did_tenant_mapping
            _TENANT_CONFIG_CACHE.pop(tenant_id, None)
            if assigned_did:
                set_did_tenant_mapping(assigned_did, tenant_id)
        except Exception:
            pass

        margin = round(max(0.0, req.retail_rate_per_min - float(reseller[1])), 2)

        return {
            "status": "success",
            "message": f"Client '{req.business_name}' onboarded successfully!",
            "client": {
                "tenant_id": tenant_id,
                "business_name": req.business_name,
                "email": req.email,
                "assigned_did": assigned_did,
                "retail_rate_per_min": req.retail_rate_per_min,
                "wholesale_rate_per_min": float(reseller[1]),
                "your_margin_per_min": margin,
                "wallet_balance": req.initial_wallet_balance,
                "api_key": api_key
            }
        }


@reseller_guard.post("/clients/{tenant_id}/rate")
def update_sub_client_rate(tenant_id: str, req: SubClientRateRequest):
    """Update retail rate per min for a sub-client."""
    if req.retail_rate_per_min <= 0:
        raise HTTPException(status_code=400, detail="Retail rate must be strictly greater than zero.")
    with sync_engine.connect() as conn:
        res = conn.execute(
            text("UPDATE tenants SET rate_per_min = :rate WHERE tenant_id = :tid"),
            {"rate": req.retail_rate_per_min, "tid": tenant_id}
        )
        conn.commit()
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Client tenant not found.")
        return {"status": "success", "message": f"Client retail rate updated to ₹{req.retail_rate_per_min:.2f}/min."}


@reseller_guard.post("/clients/{tenant_id}/topup")
def topup_sub_client_wallet(tenant_id: str, req: SubClientTopupRequest):
    """Add talktime credits to a sub-client wallet balance."""
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Top-up amount must be strictly greater than zero.")
    with sync_engine.connect() as conn:
        res = conn.execute(
            text("UPDATE tenants SET wallet_balance = wallet_balance + :amt WHERE tenant_id = :tid"),
            {"amt": req.amount, "tid": tenant_id}
        )
        conn.commit()
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Client tenant not found.")
        
        new_bal = conn.execute(
            text("SELECT wallet_balance FROM tenants WHERE tenant_id = :tid"),
            {"tid": tenant_id}
        ).scalar() or 0.0

        return {"status": "success", "message": f"Added ₹{req.amount:.2f} to client balance.", "new_balance": round(new_bal, 2)}


# -----------------------------------------------------------------------------
# 4. Whitelabel Branding Settings
# -----------------------------------------------------------------------------
@reseller_guard.post("/branding")
def update_reseller_branding(req: ResellerBrandingRequest):
    """Customize reseller white-label branding."""
    import json
    brand_dict = {
        "portal_title": req.portal_title,
        "logo_url": req.logo_url or "",
        "support_email": req.support_email or "",
        "accent_color": req.accent_color or "#6366F1"
    }
    with sync_engine.connect() as conn:
        res = conn.execute(
            text("UPDATE resellers SET branding = :brand WHERE reseller_id = :rid"),
            {"brand": json.dumps(brand_dict), "rid": req.reseller_id}
        )
        conn.commit()
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Reseller not found.")
        return {"status": "success", "message": "Whitelabel branding updated.", "branding": brand_dict}


router.include_router(reseller_guard)
