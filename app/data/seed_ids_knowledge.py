"""
Seeds the iDigitalStudies knowledge base with course content chunks.
Run: PYTHONPATH=. python app/data/seed_ids_knowledge.py
"""
from sqlalchemy import text
from app.db.session import sync_engine
from app.core.rag_engine import add_knowledge_chunk

IDS_CHUNKS = [
    {
        "source": "ids_overview.pdf",
        "text": "iDigitalStudies (IDS) is a premier digital marketing edtech platform designed for college students, freshers, entrepreneurs, and working professionals who want to master digital marketing and grow their career or business in the digital age."
    },
    {
        "source": "ids_course_curriculum.pdf",
        "text": "The Digital Marketing Master Program covers 25+ comprehensive modules including Search Engine Optimization (SEO), Google Ads, Performance Marketing, Meta Ads (Facebook and Instagram), YouTube Marketing, Content Marketing, Social Media Strategy, Email Marketing, Affiliate Marketing, Web Analytics, Google Analytics 4, E-commerce marketing, and Influencer marketing."
    },
    {
        "source": "ids_ai_tools.pdf",
        "text": "IDS provides hands-on training on 30+ cutting-edge AI tools for modern marketers: ChatGPT and Gemini for copy and SEO, Midjourney and Canva AI for visual creatives, Jasper AI for content scaling, SurferSEO for AI-assisted SEO writing, SEMrush for competitive research, Adzooma for AI PPC management, and ElevenLabs for AI audio content."
    },
    {
        "source": "ids_internship_program.pdf",
        "text": "IDS provides a guaranteed in-house internship where every enrolled student works on real client campaigns, manages live ad budgets, and builds a verified portfolio. Students receive a formal internship letter and letter of recommendation on completion."
    },
    {
        "source": "ids_duration_modes.pdf",
        "text": "The course duration is 3 to 4 months. Training is available as Live Interactive Online Batches with evening and weekend options, and as Classroom Training Batches at IDS center locations, with full recorded session backups included."
    },
    {
        "source": "ids_fees_and_offers.pdf",
        "text": "IDS offers affordable course fees with zero-cost EMI installment options. Scholarship eligibility, early bird discounts, and corporate group discounts are available. For exact current fee, customized payment plans, or scholarship evaluation, callers must speak with an admissions counselor directly."
    },
    {
        "source": "ids_certifications.pdf",
        "text": "Students earn globally recognized certifications on completion: Google Digital Marketing Certification, HubSpot Content Marketing Certification, Meta Blueprint Certification, and the IDS Digital Marketing Master Certification. These are industry-accepted credentials valued by employers."
    },
    {
        "source": "ids_placement_support.pdf",
        "text": "IDS provides 100 percent placement assistance including dedicated job portal access, resume building workshops, LinkedIn profile optimization, mock interview sessions, and direct tie-ups with 200+ hiring companies across digital agencies, startups, and corporates."
    },
    {
        "source": "ids_refund_policy.pdf",
        "text": "Refund and grievance requests must be submitted in writing to the IDS admissions committee. All refund decisions, fee concessions, and enrollment transfers require manual review and approval. Refunds cannot be processed over a voice or automated channel."
    },
    {
        "source": "ids_eligibility.pdf",
        "text": "IDS Digital Marketing Master Program is open to anyone: final-year college students, fresh graduates, working professionals looking to upskill, freelancers, business owners, and homemakers. No prior marketing or technical background is required to enroll."
    }
]

def seed_knowledge():
    tenant_id = "idigitalstudies"
    print(f"🌱 Seeding knowledge base for tenant '{tenant_id}'...")

    with sync_engine.connect() as conn:
        existing = conn.execute(
            text("SELECT COUNT(*) FROM knowledge_chunks WHERE tenant_id = :tid"),
            {"tid": tenant_id}
        ).scalar()
        if existing and existing > 0:
            print(f"⚠️  Found {existing} existing chunks. Clearing for fresh seed...")
            conn.execute(
                text("DELETE FROM knowledge_chunks WHERE tenant_id = :tid"),
                {"tid": tenant_id}
            )
            conn.commit()

    for idx, item in enumerate(IDS_CHUNKS, 1):
        print(f"  [{idx}/{len(IDS_CHUNKS)}] Embedding: {item['source']} ...")
        add_knowledge_chunk(
            tenant_id=tenant_id,
            source_doc=item["source"],
            chunk_text=item["text"]
        )

    print(f"\n✅ Successfully embedded and stored {len(IDS_CHUNKS)} knowledge chunks!")

if __name__ == "__main__":
    seed_knowledge()
