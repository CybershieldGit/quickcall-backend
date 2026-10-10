"""
Activate Phono Tel Tenant & DID Routing
Maps:
- 080-472-84391 (+918047284391) -> iDigitalStudies (IDS)
- 095-138-86363 (+919513886363) -> Phono Tel (Enterprise IVR & AI Voicebot Solutions)
"""
import json
from sqlalchemy import text
from app.db.session import sync_engine

def activate_tenants():
    print("🚀 Connecting to PostgreSQL to configure multi-tenant routing...")
    with sync_engine.connect() as conn:
        # 1. Ensure iDigitalStudies is properly mapped to +918047284391
        conn.execute(
            text("""
            INSERT INTO tenant_phone_numbers (number, tenant_id, vendor, is_active)
            VALUES (:num, 'idigitalstudies', 'exotel', true)
            ON CONFLICT (number) DO UPDATE SET tenant_id = 'idigitalstudies', is_active = true
            """),
            {"num": "+918047284391"}
        )
        print("✅ Mapped +918047284391 -> iDigitalStudies (IDS)")

        # 2. Register Phono Tel Tenant
        conn.execute(
            text("""
            INSERT INTO tenants (tenant_id, business_name, status, telephony_mode)
            VALUES ('phonotel', 'Phono Tel', 'active', 'master_pool')
            ON CONFLICT (tenant_id) DO UPDATE SET business_name = 'Phono Tel', status = 'active'
            """)
        )
        print("✅ Registered tenant 'phonotel' in tenants table")

        # 3. Map the second ExoPhone: 095-138-86363 -> Phono Tel
        conn.execute(
            text("""
            INSERT INTO tenant_phone_numbers (number, tenant_id, vendor, is_active)
            VALUES (:num, 'phonotel', 'exotel', true)
            ON CONFLICT (number) DO UPDATE SET tenant_id = 'phonotel', is_active = true
            """),
            {"num": "+919513886363"}
        )
        print("✅ Mapped +919513886363 -> Phono Tel")

        # 4. Configure Phono Tel Persona & Business Rules
        persona_text = (
            "You are 'Rohan', the senior enterprise AI solutions advisor at Phono Tel. "
            "Phono Tel provides next-generation cloud IVR services, conversational AI calling and chat agents, "
            "and seamless call automation workflows for businesses and enterprises. "
            "Core Offerings: Smart Multi-level Cloud IVR, Real-time Conversational Voicebots, Automated Outbound Dialing (OBD), "
            "and Seamless CRM/Telephony Integrations. "
            "Your goal is to warmly welcome callers, understand their business calling requirements, and explain how Phono Tel can automate their customer calls. "
            "CRITICAL RULES: "
            "1. Keep answers concise (1 to 2 spoken sentences maximum per turn) suitable for a live phone conversation. "
            "2. Never make up custom SLA pricing or give unauthorized discounts. If the caller asks for custom enterprise contract pricing or wants to speak to sales, offer to connect them to a sales manager immediately."
        )

        business_rules = {
            "services": [
                "Smart Cloud IVR & Virtual Receptionist",
                "Conversational AI Voice & Chat Agents",
                "Automated Outbound Calling & Surveys",
                "CRM Integration & Call Analytics"
            ],
            "target_customers": "Enterprises, SMEs, Startups looking to automate inbound and outbound phone support",
            "team_routing": [
                {
                    "department": "Enterprise Sales & Solutions",
                    "phone": "+917022721098",
                    "keywords": ["sales", "pricing", "cost", "demo", "quotation", "enterprise", "plan"]
                },
                {
                    "department": "Technical Support",
                    "phone": "+917022721098",
                    "keywords": ["support", "integration", "api", "setup", "issue", "troubleshoot"]
                }
            ]
        }

        # Check if config exists, update or insert
        conn.execute(
            text("""
            INSERT INTO tenant_config (config_id, tenant_id, version, persona, business_rules, escalation_number, guardrail_topics, is_active)
            VALUES ('cfg_phonotel_default', 'phonotel', 1, :persona, :rules, :esc_num, :guardrails, true)
            ON CONFLICT (config_id) DO UPDATE SET 
                persona = :persona,
                business_rules = :rules,
                escalation_number = :esc_num,
                is_active = true
            """),
            {
                "persona": persona_text,
                "rules": json.dumps(business_rules),
                "esc_num": "+917022721098",
                "guardrails": json.dumps(["custom_contract_dispute", "unauthorized_discount", "legal_dispute"])
            }
        )
        conn.commit()
        print("✅ Configured Phono Tel persona, business rules, and escalation routing!")

if __name__ == "__main__":
    activate_tenants()
