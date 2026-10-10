#!/usr/bin/env python3
"""
Ingest Official IDS 21-Page PDF Knowledge Base into PostgreSQL + pgvector (JSONB embeddings).
Matches the comprehensive official RAG Knowledge Base, Call-Handling Rules & Approved Information Guide.
"""
import os
import sys
import json
import uuid

# Add parent directory to path
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
sys.path.insert(0, parent_dir)

from sqlalchemy import text
from app.db.session import sync_engine
from app.core.rag_engine import embed_text, warmup_rag

TENANT_ID = "idigitalstudies"

CHUNKS = [
    {
        "source_doc": "ids_identity_and_location.pdf",
        "chunk_text": (
            "Institute of Digital Studies (IDS) is an AI-integrated digital marketing training institute established in 2016, "
            "operating as a unit of Cybershield Technologies Pvt. Ltd. Official website: https://idigitalstudies.com/. "
            "Head office and training center address: T3, B1603, NXOne, Tech Zone 4, opposite Gaur City Mall, Greater Noida, "
            "Uttar Pradesh 201306. Published contact phone: +91 9315471293, and official contact email: info@idigitalstudies.com. "
            "Training center operating hours are 9:00 AM to 9:00 PM. IDS delivers practical, industry-oriented digital marketing "
            "education with live client projects, tool mastery, industry certifications, and dedicated placement assistance."
        )
    },
    {
        "source_doc": "ids_course_catalog_and_durations.pdf",
        "chunk_text": (
            "IDS offers four comprehensive digital marketing courses:\n"
            "1. Master in Digital Marketing: 6 months duration, available Offline classroom and Live Online. Includes 10+ live projects, "
            "bilingual Hindi/English instruction, and a 3-month in-house internship covering SEO, SEM, social media, content marketing, and analytics.\n"
            "2. Digital Marketing Specialist: 3 months duration, available Offline and Online. Includes 5+ live projects covering SEO, PPC Google Ads, "
            "social media marketing, email marketing, and web analytics.\n"
            "3. Professional / Business Owner Course: Customised timeline with 1:1 personalised digital marketing coaching tailored to business goals, "
            "growth strategy, and 10+ live projects.\n"
            "4. Customised Course in Digital Marketing: Customised timeline available Offline and Online, tailored to learner goals, individual pace, and custom projects."
        )
    },
    {
        "source_doc": "ids_curriculum_and_modules.pdf",
        "chunk_text": (
            "The IDS curriculum features 50+ industry-aligned modules across three learning pillars:\n"
            "• Core Digital Strategy: Digital Marketing Strategy, No-Code Website Designing, Practical Foundations, Email Marketing, Inbound Marketing, "
            "Search Engine Optimization (SEO), Google Webmaster Tools, Search Engine Algorithms, Local Business Listings, and Google Analytics.\n"
            "• Performance & Advertising: Competitor Analysis, Social Media Marketing, Video Marketing Fundamentals, E-commerce Marketing, Mobile Marketing, "
            "Conversion Rate Optimization (CRO), Google Ads (Search, Display, Video), and Online Reputation Management (ORM).\n"
            "• Growth & Monetization: Live Practical Sessions, Real-World Case Studies, Blogging, AdSense, Affiliate Marketing, Freelancer Growth, "
            "Search & Social Parameters, Remarketing Concepts, and Digital Marketing Interview Preparation."
        )
    },
    {
        "source_doc": "ids_tools_and_platforms.pdf",
        "chunk_text": (
            "IDS provides hands-on practical mastery of leading industry marketing tools and AI platforms:\n"
            "• SEO & Analytics: SEMrush, Google Analytics, Google Search Console, Microsoft Clarity, Hotjar, SurferSEO.\n"
            "• Advertising & Social Media: Google Ads, Meta Ads Manager, Hootsuite, Canva, Adzooma.\n"
            "• Marketing Automation & E-Commerce: HubSpot, HubSpot Marketing Hub, Mailchimp, WordPress, Shopify, Pabbly, Refersion.\n"
            "• AI-Powered Marketing: ChatGPT, Midjourney, Jasper AI, Canva AI, and AI-driven campaign and content workflows."
        )
    },
    {
        "source_doc": "ids_learning_modes_and_eligibility.pdf",
        "chunk_text": (
            "Learning Modes, Batch Schedule & Eligibility at IDS:\n"
            "• Training Modes: Available In-Class (offline at our Greater Noida NXOne center) and Live Interactive Online. "
            "Center timings are 9:00 AM to 9:00 PM with Weekday and Weekend batch options, including alternate-day schedules.\n"
            "• Who Can Join & Eligibility: Open to learners of any educational background—college students, freshers, job seekers, working professionals, "
            "freelancers, housewives, business owners, and career switchers. No prior coding or technical knowledge is required; only basic smartphone "
            "and internet literacy is needed."
        )
    },
    {
        "source_doc": "ids_internship_and_stipend.pdf",
        "chunk_text": (
            "IDS provides a guaranteed in-house internship (included as a 3-month internship in the 6-month Master course) where enrolled students "
            "work directly on real client campaigns, manage live advertising budgets, and build a verified professional portfolio. "
            "Stipend is performance-based and decided according to candidate performance and the internship interview. "
            "Students receive a formal internship completion certificate and a letter of recommendation upon finishing."
        )
    },
    {
        "source_doc": "ids_placement_and_career_support.pdf",
        "chunk_text": (
            "IDS provides dedicated placement assistance and career mentorship. The institute features a published 90% placement success rate, "
            "partnerships with 50+ hiring companies, and over 2000+ students placed. Placement support includes resume building, portfolio curation, "
            "LinkedIn optimization, mock interview drills, and company recruitment drives. Placement assistance is provided to eligible students meeting "
            "attendance (95-100%) and project completion criteria; final job offers depend on candidate interview performance and employer requirements."
        )
    },
    {
        "source_doc": "ids_alumni_and_success_stories.pdf",
        "chunk_text": (
            "IDS alumni work across leading organizations and fast-growing tech companies, including Salesforce, Meesho, Dream11, Zomato, "
            "Razorpay, Medianet, Testbook, Nykaa, Flipkart, TCS, Paytm, and Myntra. Course cards display average salary outcomes of up to 8.5 LPA "
            "for Master course graduates and 6.2 LPA for Specialist course graduates, which vary depending on individual skills, experience, and employer assessments."
        )
    },
    {
        "source_doc": "ids_certifications_and_credentials.pdf",
        "chunk_text": (
            "Students graduating from IDS receive the official IDS Digital Marketing Master Certification. In addition, students are fully prepared "
            "and mentored to achieve globally recognized industry certifications including Google Ads & Analytics Certifications, HubSpot Content & Inbound "
            "Marketing Certifications, and Meta Blueprint Certification. IDS is MSME recognized and affiliated with Skill India and NSDC initiatives."
        )
    },
    {
        "source_doc": "ids_fees_installment_and_emi.pdf",
        "chunk_text": (
            "Course Fees, Discounts and Payment Options:\n"
            "IDS provides flexible payment plans with installment options and zero-cost EMI to make education accessible. "
            "Exact course fee amounts, customized installment schedules, early bird discounts, and scholarship evaluations are dynamic "
            "and finalized with an admissions counselor. The AI agent connects callers to a senior counselor for exact fee quotes and financial arrangements."
        )
    },
    {
        "source_doc": "ids_refund_and_cancellation_policy.pdf",
        "chunk_text": (
            "Official IDS Refund & Cancellation Policy (dated 1 April 2025):\n"
            "• Registration Fee: INR 1,000 is strictly non-refundable.\n"
            "• Early Cancellation: Within 7 days of enrollment, if no batch is assigned or a batch is assigned but 0 classes are attended, "
            "a full tuition refund is issued (excluding INR 1,000 registration fee and 18% GST deduction).\n"
            "• Active Students: After 3 classes and first installment, attending up to 4 classes allows up to 25% tuition refund subject to management approval; "
            "attending 5 or more classes yields no refund.\n"
            "• One-time Payment: Up to 8 classes allows 50% refund; up to 12 classes allows 25% refund; more than 12 classes yields no refund.\n"
            "• Approved refunds are credited to the original payment method within 7 to 10 business days. Fees are non-transferable."
        )
    },
    {
        "source_doc": "ids_demo_counselling_and_admissions.pdf",
        "chunk_text": (
            "Free Demo, 1-on-1 Counselling & Admissions:\n"
            "IDS offers prospective students a free demo class and 1-on-1 career counselling session with our expert faculty to review the curriculum, "
            "tool training, and live campaign roadmap. Callers interested in scheduling a free demo or speaking with an admissions executive can provide "
            "their name and contact number for a prompt callback."
        )
    },
    {
        "source_doc": "ids_live_projects_and_practical_work.pdf",
        "chunk_text": (
            "Live Projects & Practical Campaign Experience:\n"
            "Practical exposure is central to IDS training: Master students complete 10+ live projects, Specialist students complete 5+ live projects, "
            "and Business Owner participants complete 10+ custom brand growth projects. Students work with real ad spend, audit live websites for SEO, "
            "manage conversion funnels, and generate real-time analytics reports."
        )
    },
    {
        "source_doc": "ids_call_handling_and_transfer_rules.pdf",
        "chunk_text": (
            "Call Handling, Transfer & Escalation Protocols:\n"
            "• P1 Immediate Escalation: Caller explicitly requests to speak with a human, executive, or counselor; complaints or angry callers; "
            "payment failures, refund disputes, or cancellation requests; demands for 100% placement or fixed salary guarantees.\n"
            "• P2 Transfer Topics: Exact course fee quotes, commercial fee negotiations or discounts; specific batch seat availability or personal career predictions.\n"
            "• Conversational Rule: The AI speaks naturally and warmly in 1-2 concise sentences without monologues, maintains conversational flow, and avoids repeating previous statements."
        )
    }
]

def main():
    print(f"🚀 Ingesting {len(CHUNKS)} official knowledge chunks for '{TENANT_ID}'...")
    
    with sync_engine.connect() as conn:
        # Delete existing chunks for this tenant to avoid stale data
        conn.execute(
            text("DELETE FROM knowledge_chunks WHERE tenant_id = :tid"),
            {"tid": TENANT_ID}
        )
        conn.commit()
        print(f"🗑️ Cleared previous chunks for '{TENANT_ID}'.")
        
        # Insert each new chunk with fresh vector embedding
        for i, chunk in enumerate(CHUNKS):
            chunk_id = uuid.uuid4().hex
            doc_name = chunk["source_doc"]
            txt = chunk["chunk_text"]
            
            print(f"[{i+1}/{len(CHUNKS)}] Embedding {doc_name} ({len(txt)} chars)...")
            vec = embed_text(txt)
            
            conn.execute(
                text(
                    "INSERT INTO knowledge_chunks (chunk_id, tenant_id, source_doc, chunk_text, embedding) "
                    "VALUES (:cid, :tid, :doc, :txt, :emb)"
                ),
                {
                    "cid": chunk_id,
                    "tid": TENANT_ID,
                    "doc": doc_name,
                    "txt": txt,
                    "emb": json.dumps(vec)
                }
            )
            conn.commit()
            
        print(f"✅ Successfully inserted and indexed {len(CHUNKS)} knowledge chunks into PostgreSQL!")

        # Update tenant_config with updated official persona & guidelines
        official_persona = (
            "You are Aanya, the friendly, professional, and knowledgeable AI admissions advisor for Institute of Digital Studies (IDS). "
            "IDS is an AI-integrated digital marketing training institute established in 2016 (a unit of Cybershield Technologies Pvt. Ltd.), "
            "located at NXOne, Greater Noida. "
            "Key Offerings: Master in Digital Marketing (6 months, 10+ live projects, 3-month in-house internship), "
            "Digital Marketing Specialist (3 months, 5+ live projects), Professional/Business Owner Coaching (1:1 online), "
            "and Customised Courses. Hands-on mastery of 30+ AI tools (ChatGPT, Midjourney, Jasper AI, SurferSEO, SEMrush, Canva AI, Adzooma, etc.), "
            "and 50+ industry-aligned modules across SEO, Google Ads, Social Media, and E-commerce. "
            "Internship & Stipend: Master course includes a 3-month in-house internship on real client campaigns; stipend is performance-based, "
            "decided according to candidate performance and the internship interview. "
            "Placement: 90% placement success rate with 50+ hiring partners and 2000+ placed students. Dedicated placement assistance is provided "
            "to eligible students (not an unconditional individual guarantee). "
            "Your Goal: Answer caller queries warmly, naturally, and concisely in 1 to 2 spoken sentences (20-35 words). "
            "CRITICAL RULES: "
            "1. Keep responses conversational and natural. Never cut off mid-sentence. "
            "2. Never repeat the exact same sentence or phrasing you already said earlier in the conversation. "
            "3. If asked about exact course fees, discounts, refund calculations, or if caller asks to speak to an executive/human, "
            "politely offer to connect them to a senior admissions counselor."
        )
        
        conn.execute(
            text(
                "UPDATE tenant_config "
                "SET persona = :p "
                "WHERE tenant_id = :tid"
            ),
            {"p": official_persona, "tid": TENANT_ID}
        )
        conn.commit()
        print("✅ Updated tenant_config persona with official 21-page PDF guidelines!")

    # Pre-warm in-memory cache
    warmup_rag(TENANT_ID)
    print("🎉 All RAG knowledge base updates completed successfully!")

if __name__ == "__main__":
    main()
