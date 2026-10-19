import sys
import os
from sqlalchemy import text
from app.config import settings
from app.db.session import sync_engine, Base
from app.db.models import Tenant, TenantPhoneNumber, TenantConfig, Agent

def init_database():
    print("🔌 Connecting to PostgreSQL database...")
    try:
        print("📦 Creating all tables...")
        Base.metadata.create_all(bind=sync_engine)
        print("✅ Database tables created successfully.")

        # Ensure executive notes, agent_id, and summary columns exist in calls, and assigned_agent_id in tenant_phone_numbers
        with sync_engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS agents (
                    agent_id VARCHAR(64) PRIMARY KEY,
                    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id),
                    name VARCHAR(128) NOT NULL,
                    role_title VARCHAR(128) DEFAULT 'Admissions Counselor',
                    persona TEXT NOT NULL,
                    greeting TEXT NOT NULL,
                    escalation_number VARCHAR(32),
                    voice_id VARCHAR(64) DEFAULT 'default',
                    language VARCHAR(32) DEFAULT 'hi-IN',
                    is_default BOOLEAN DEFAULT false,
                    is_active BOOLEAN DEFAULT true,
                    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS ix_agents_tenant_id ON agents (tenant_id);
                ALTER TABLE tenant_phone_numbers ADD COLUMN IF NOT EXISTS assigned_agent_id VARCHAR(64);
                ALTER TABLE tenant_phone_numbers ADD COLUMN IF NOT EXISTS sip_extension VARCHAR(32);
                ALTER TABLE tenant_phone_numbers ADD COLUMN IF NOT EXISTS sip_password VARCHAR(256);
                ALTER TABLE phone_number_pool ADD COLUMN IF NOT EXISTS sip_password VARCHAR(256);
                ALTER TABLE tenant_phone_numbers ALTER COLUMN sip_password TYPE VARCHAR(256);
                ALTER TABLE phone_number_pool ALTER COLUMN sip_password TYPE VARCHAR(256);
                ALTER TABLE calls ADD COLUMN IF NOT EXISTS agent_id VARCHAR(64);
                ALTER TABLE calls ADD COLUMN IF NOT EXISTS summary TEXT;
                ALTER TABLE calls ADD COLUMN IF NOT EXISTS primary_concern VARCHAR(255);
                ALTER TABLE calls ADD COLUMN IF NOT EXISTS caller_sentiment VARCHAR(64);
                ALTER TABLE calls ADD COLUMN IF NOT EXISTS action_required TEXT;
                ALTER TABLE calls ADD COLUMN IF NOT EXISTS executive_notes TEXT;
                ALTER TABLE tenants ADD COLUMN IF NOT EXISTS api_key VARCHAR(128);
                ALTER TABLE tenants ADD COLUMN IF NOT EXISTS reseller_id VARCHAR(64);
                ALTER TABLE tenants ADD COLUMN IF NOT EXISTS rate_per_min FLOAT DEFAULT 5.0;
                ALTER TABLE tenants ADD COLUMN IF NOT EXISTS wallet_balance FLOAT DEFAULT 500.0;
                ALTER TABLE tenants ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255);
            """))
            conn.commit()
        print("✅ Schema migrations applied for agents, calls, and tenants tables.")

        # Seed / secure the Platform SuperAdmin and Demo Reseller (never with default credentials)
        with sync_engine.connect() as conn:
            import secrets as _secrets
            from app.core.auth import hash_password, verify_password, KNOWN_DEFAULT_PASSWORDS

            def _is_weak(stored: str) -> bool:
                if not stored:
                    return True
                if not stored.startswith("pbkdf2:sha256:"):
                    return True  # legacy plaintext
                return any(verify_password(d, stored) for d in KNOWN_DEFAULT_PASSWORDS)

            def _replacement_hash() -> str:
                # Use ADMIN_PASSWORD when it's strong; otherwise an unusable random hash (reset via scripts/set_password.py)
                env_pwd = settings.ADMIN_PASSWORD or ""
                if len(env_pwd) >= 8 and env_pwd.lower() not in KNOWN_DEFAULT_PASSWORDS:
                    return hash_password(env_pwd)
                return hash_password(_secrets.token_urlsafe(32))

            admin_check = conn.execute(text("SELECT admin_id, password_hash FROM admin_users WHERE email = :e"), {"e": settings.ADMIN_EMAIL}).fetchone()
            if not admin_check:
                conn.execute(
                    text("""
                        INSERT INTO admin_users (admin_id, email, password_hash, name, role)
                        VALUES ('admin_root', :e, :pwd, 'Platform SuperAdmin', 'superadmin')
                        ON CONFLICT (email) DO NOTHING
                    """),
                    {"e": settings.ADMIN_EMAIL, "pwd": _replacement_hash()}
                )
                print(f"👑 Seeded Platform SuperAdmin ({settings.ADMIN_EMAIL}).")
            elif _is_weak(admin_check[1]):
                conn.execute(
                    text("UPDATE admin_users SET password_hash = :pwd WHERE admin_id = :aid"),
                    {"pwd": _replacement_hash(), "aid": admin_check[0]}
                )
                print("🔒 Replaced default/plaintext SuperAdmin password.")

            # Accounts still on default passwords can no longer log in; flag them for reset
            for tbl, idc in (("tenants", "tenant_id"), ("resellers", "reseller_id")):
                for rid, stored in conn.execute(text(f"SELECT {idc}, password_hash FROM {tbl}")).fetchall():
                    if _is_weak(stored):
                        print(f"⚠️ {tbl}.{rid} uses a default or missing password. Set one with: python scripts/set_password.py {tbl[:-1]} {rid}")

            # Rotate the publicly known demo reseller API key
            conn.execute(
                text("UPDATE resellers SET api_key = :k WHERE api_key = 'qck_reseller_apex99'"),
                {"k": f"qck_res_{_secrets.token_hex(16)}"}
            )
            conn.commit()

        # Seed Telephony Phone Number Inventory Pool (available for assignment via frontend)
        with sync_engine.connect() as conn:
            initial_pool = [
                ("+917935486072", "rpdigital", "08239"),
                ("08239", "rpdigital", "08239"),
                ("+919513886363", "exotel", None),
                ("+918037091588", "exotel", None),
                ("+917314624575", "rpdigital", None),
                ("+918047284391", "exotel", None),
                ("+917022721098", "rpdigital", None),
                ("+917316920764", "rpdigital", None),
            ]
            for item in initial_pool:
                num = item[0]
                vendor = item[1]
                ext = item[2]
                conn.execute(
                    text("""
                        INSERT INTO phone_number_pool (number, vendor, sip_extension, status, assigned_tenant_id)
                        VALUES (:num, :vendor, :ext, 'available', NULL)
                        ON CONFLICT (number) DO NOTHING
                    """),
                    {"num": num, "vendor": vendor, "ext": ext}
                )
            conn.commit()
        # Seed Default Agents for iDigital Studies (Admissions Counselor + Placement Counselor)
        with sync_engine.connect() as conn:
            # Find the active tenant ID for iDigital Studies
            ids_tenant = conn.execute(
                text("SELECT tenant_id FROM tenants WHERE tenant_id IN ('cl_idigitalstudie_82d5', 'idigitalstudies') ORDER BY tenant_id ASC LIMIT 1")
            ).fetchone()
            if ids_tenant:
                t_id = ids_tenant[0]
                agents_data = [
                    (
                        "ag_ids_admissions",
                        t_id,
                        "Aanya",
                        "Admissions Counselor",
                        "You are Aanya, a warm, professional, and encouraging admissions counselor at iDigital Studies. Your goal is to guide prospective students on digital marketing courses, curriculum, eligibility, batch timings, and admission steps. If the caller asks about placements, share placement statistics and hiring partners from our knowledge base.",
                        "Hello, I am Aanya from iDigital Studies, how can I help you with course admissions today?",
                        "+919560732274",
                        True
                    ),
                    (
                        "ag_ids_placement",
                        t_id,
                        "Rohan",
                        "Placement & Career Counselor",
                        "You are Rohan, dedicated Placement and Career Counselor at iDigital Studies. Your goal is to advise students on job opportunities, our 50+ hiring partner companies (like TCS, Wipro, Infosys), 90% placement success rate, internship projects, resume reviews, and placement assistance process. If the caller asks for admissions, direct them to our admissions team.",
                        "Hello, I am Rohan, Placement Counselor at iDigital Studies. How can I assist you with internship and job placement queries today?",
                        "+919560732274",
                        False
                    )
                ]
                for ag_id, tid, name, role, persona, greeting, esc, is_def in agents_data:
                    conn.execute(
                        text("""
                            INSERT INTO agents (agent_id, tenant_id, name, role_title, persona, greeting, escalation_number, is_default, is_active)
                            VALUES (:ag_id, :tid, :name, :role, :persona, :greeting, :esc, :is_def, true)
                            ON CONFLICT (agent_id) DO NOTHING
                        """),
                        {"ag_id": ag_id, "tid": tid, "name": name, "role": role, "persona": persona, "greeting": greeting, "esc": esc, "is_def": is_def}
                    )
                # Assign default agent to any unassigned active phone numbers for this tenant
                conn.execute(
                    text("""
                        UPDATE tenant_phone_numbers 
                        SET assigned_agent_id = 'ag_ids_admissions' 
                        WHERE tenant_id = :tid AND (assigned_agent_id IS NULL OR assigned_agent_id = '')
                    """),
                    {"tid": t_id}
                )
                conn.commit()
                print("🎓 Seeded default AI Agents for iDigital Studies (Aanya: Admissions & Rohan: Placements).")

        print("✨ Database ready for fresh tenant onboarding via front end.")

    except Exception as e:
        print(f"❌ Error initializing database: {e}")
        raise e

if __name__ == "__main__":
    init_database()
