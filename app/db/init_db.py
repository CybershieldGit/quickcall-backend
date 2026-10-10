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
            """))
            conn.commit()
        print("✅ Schema migrations applied for agents, calls, and tenants tables.")

        # Seed Default Platform SuperAdmin & Demo Reseller
        with sync_engine.connect() as conn:
            from app.core.auth import hash_password
            # Seed SuperAdmin
            admin_check = conn.execute(text("SELECT admin_id, password_hash FROM admin_users WHERE email = 'admin@qwickcall.ai'")).fetchone()
            if not admin_check:
                conn.execute(
                    text("""
                        INSERT INTO admin_users (admin_id, email, password_hash, name, role)
                        VALUES ('admin_root', 'admin@qwickcall.ai', :pwd, 'Platform SuperAdmin', 'superadmin')
                        ON CONFLICT (email) DO NOTHING
                    """),
                    {"pwd": hash_password("admin123")}
                )
                print("👑 Seeded Platform SuperAdmin (admin@qwickcall.ai).")
            elif admin_check[1] == "admin123":
                conn.execute(
                    text("UPDATE admin_users SET password_hash = :pwd WHERE email = 'admin@qwickcall.ai'"),
                    {"pwd": hash_password("admin123")}
                )

            # Seed Demo Reseller
            reseller_check = conn.execute(text("SELECT reseller_id, password_hash FROM resellers WHERE reseller_id = 'partner_apex'")).fetchone()
            if not reseller_check:
                conn.execute(
                    text("""
                        INSERT INTO resellers (reseller_id, name, company_name, email, password_hash, api_key, status, wholesale_rate_per_min, wallet_balance, branding)
                        VALUES ('partner_apex', 'Rajesh Sharma', 'Apex AI Voice Solutions', 'partner@apexsolutions.in', :pwd, 'qck_reseller_apex99', 'active', 3.00, 2500.0, '{"portal_title": "Apex AI Voice Partner", "logo_url": "", "support_email": "support@apexsolutions.in"}')
                        ON CONFLICT (reseller_id) DO NOTHING
                    """),
                    {"pwd": hash_password("partner123")}
                )
                print("🤝 Seeded Demo Reseller partner_apex (wholesale rate: ₹3.00/min).")
            elif reseller_check[1] == "partner123":
                conn.execute(
                    text("UPDATE resellers SET password_hash = :pwd WHERE reseller_id = 'partner_apex'"),
                    {"pwd": hash_password("partner123")}
                )
            conn.commit()

        # Seed Telephony Phone Number Inventory Pool (available for assignment via frontend)
        with sync_engine.connect() as conn:
            initial_pool = [
                ("+917935486072", "rpdigital", "08239"),
                ("+917935486072", "rpdigital", None),
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
