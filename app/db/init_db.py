import sys
import os
from sqlalchemy import text
from app.db.session import sync_engine, Base
from app.db.models import Tenant, TenantPhoneNumber, TenantConfig

def init_database():
    print("🔌 Connecting to PostgreSQL database...")
    try:
        print("📦 Creating all tables...")
        Base.metadata.create_all(bind=sync_engine)
        print("✅ Database tables created successfully.")

        # Seed iDigitalStudies Tenant
        with sync_engine.connect() as conn:
            tenant_check = conn.execute(text("SELECT tenant_id FROM tenants WHERE tenant_id = 'idigitalstudies'")).fetchone()
            if not tenant_check:
                print("🌱 Seeding iDigitalStudies tenant & config...")
                conn.execute(
                    text("INSERT INTO tenants (tenant_id, business_name, status) VALUES (:id, :name, :status)"),
                    {"id": "idigitalstudies", "name": "iDigitalStudies (IDS)", "status": "active"}
                )
                
                # Link Twilio phone number
                conn.execute(
                    text("""
                    INSERT INTO tenant_phone_numbers (number, tenant_id, vendor, is_active) 
                    VALUES (:num, :tenant_id, 'twilio', true)
                    ON CONFLICT (number) DO NOTHING
                    """),
                    {"num": "+12296336435", "tenant_id": "idigitalstudies"}
                )

                # Seed Persona and Guardrails
                persona_text = (
                    "You are 'Aanya', the friendly and knowledgeable AI admissions advisor for iDigitalStudies (IDS). "
                    "iDigitalStudies is a premier digital marketing edtech platform that helps students, freshers, and working professionals master modern digital marketing. "
                    "Key Offerings: Advanced Digital Marketing Master Certification, Hands-on mastery of 30+ AI tools (ChatGPT, Midjourney, Jasper AI, SurferSEO, SEMrush, Canva AI, Adzooma, etc.), and guaranteed In-House Internship working on real client campaigns. "
                    "Your goal is to answer caller queries warmly, accurately, and concisely (1-2 short spoken sentences max). "
                    "CRITICAL RULES: "
                    "1. Keep responses ultra-concise (< 2 short conversational sentences) suitable for a phone call. "
                    "2. Zero extrapolation on custom fee discounts, 100% refund disputes, or job placement guarantees. If caller demands fee negotiation or formal refund, offer to connect them to a senior admissions counselor. "
                    "3. If the caller asks to speak to a person/counselor, smoothly transition them."
                )

                conn.execute(
                    text("""
                    INSERT INTO tenant_config (config_id, tenant_id, version, persona, business_rules, escalation_number, guardrail_topics, is_active)
                    VALUES (:cfg_id, :tenant_id, 1, :persona, :rules, :esc_num, :guardrails, true)
                    """),
                    {
                        "cfg_id": "cfg_ids_default",
                        "tenant_id": "idigitalstudies",
                        "persona": persona_text,
                        "rules": '{"course_duration": "3 to 4 months", "modes": "Live Online & Classroom Batches", "internship": "Guaranteed In-house internship on live client projects", "ai_tools": "30+ AI tools included with lifetime prompt library"}',
                        "esc_num": "+919876543210",
                        "guardrails": '["refund_negotiation", "price_bargaining", "placement_guarantee", "dispute"]'
                    }
                )
                conn.commit()
                print("✅ Seeded tenant 'idigitalstudies' successfully.")
            else:
                print("ℹ️ Tenant 'idigitalstudies' already exists.")

    except Exception as e:
        print(f"❌ Error initializing database: {e}")
        raise e

if __name__ == "__main__":
    init_database()
