"""
Executive Summarizer Service
Produces structured briefing notes, caller sentiment, and action items via Groq.
Models:
- Primary: llama-3.3-70b-versatile
- Fallback: llama-3.1-8b-instant
"""
import json
import re
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List
from groq import Groq
from sqlalchemy import text
from app.config import settings
from app.db.session import sync_engine

PRIMARY_SUMMARY_MODEL = "qwen/qwen3.8-27b"
FALLBACK_SUMMARY_MODEL = "openai/gpt-oss-120b"


def generate_executive_summary(
    call_id: str,
    tenant_id: str = "idigitalstudies",
    history: Optional[List[Dict[str, str]]] = None,
    outcome: str = "completed"
) -> Dict[str, Any]:
    """
    Analyzes conversation history and generates a structured executive briefing note & summary:
    - primary_concern: single sentence defining what the caller wanted/worried about
    - caller_sentiment: Interested | Skeptical | Inquiring | Urgent/Escalated | Neutral
    - action_required: concrete next step for executive/counselor
    - summary: 2-3 sentence overview
    - executive_notes: formatted markdown briefing card
    Persists these to the calls table (and updates lead table if lead exists).
    """
    conv_history = history
    if not conv_history:
        try:
            with sync_engine.connect() as conn:
                rows = conn.execute(
                    text("SELECT speaker, text FROM call_transcripts WHERE call_id = :cid ORDER BY turn_index ASC"),
                    {"cid": call_id}
                ).fetchall()
                conv_history = [{"role": ("user" if r[0] == "caller" else "assistant"), "content": r[1]} for r in rows]
        except Exception as e:
            print(f"[Summary History Load Error] {e}")
            conv_history = []

    user_turns = [t["content"] for t in (conv_history or []) if t.get("role") == "user" and t.get("content", "").strip()]
    if not user_turns:
        res = {
            "primary_concern": "Caller disconnected without speaking / no audible query.",
            "caller_sentiment": "Unknown / Dropped",
            "action_required": "Follow up with introductory SMS or WhatsApp brochure.",
            "summary": "The call connected and initial greeting was played, but the caller disconnected before speaking.",
            "executive_notes": "• Status: Dropped Call\n• Caller spoke: No\n• Next Action: Send WhatsApp introduction with syllabus."
        }
    else:
        dialogue_text = "\n".join([f"{'Caller' if t.get('role') == 'user' else 'AI Agent'}: {t.get('content', '')}" for t in conv_history])

        system_prompt = (
            "You are an expert admissions & customer intelligence analyst for iDigitalStudies (IDS).\n"
            "Your job is to analyze the phone conversation transcript between an AI admissions advisor and a prospective student/caller.\n"
            "Produce an actionable, high-clarity Executive Briefing Note so that human admissions counselors and managers can immediately understand the caller's concern and take the right next steps without listening to the audio.\n\n"
            "You MUST respond ONLY with valid JSON in this exact structure:\n"
            "{\n"
            '  "primary_concern": "A single direct sentence summarizing the main question, problem, or objective of the caller.",\n'
            '  "caller_sentiment": "One of: Highly Interested | Moderately Interested | Skeptical / Price Sensitive | Confused | Escalated / Frustrated",\n'
            '  "action_required": "Specific actionable next step for the counselor.",\n'
            '  "summary": "2 to 3 sentences summarizing the conversation flow, questions asked, agent answers, and final outcome.",\n'
            '  "executive_notes": "Neat bullet points highlighting Key Questions, Stance, and Priority."\n'
            "}"
        )

        user_prompt = f"Call ID: {call_id}\nOutcome: {outcome}\n\nTranscript:\n{dialogue_text}\n\nGenerate the JSON executive briefing note:"

        res = None
        if settings.GROQ_API_KEY:
            client = Groq(api_key=settings.GROQ_API_KEY)
            for model_name in [PRIMARY_SUMMARY_MODEL, FALLBACK_SUMMARY_MODEL, "openai/gpt-oss-20b"]:
                try:
                    resp = client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        temperature=0.2,
                        max_tokens=350,
                        response_format={"type": "json_object"}
                    )
                    raw_content = resp.choices[0].message.content.strip()
                    res = json.loads(raw_content)
                    break
                except Exception as e:
                    print(f"[LLM Summarize Error on {model_name}] {e}")

        if not res:
            last_caller_query = user_turns[-1] if user_turns else "General Inquiry"
            res = {
                "primary_concern": f"Caller inquired: {last_caller_query[:100]}",
                "caller_sentiment": "Inquiring",
                "action_required": "Counselor callback recommended to discuss curriculum and enrollment.",
                "summary": f"Caller had a voice conversation with admissions AI ({len(user_turns)} caller turns recorded).",
                "executive_notes": f"• Queries asked: {len(user_turns)}\n• Latest query: {last_caller_query}\n• Action: Counselor follow-up required"
            }

    res.setdefault("primary_concern", "General admissions inquiry.")
    res.setdefault("caller_sentiment", "Inquiring")
    res.setdefault("action_required", "Follow up with caller.")
    res.setdefault("summary", "Caller spoke with admissions AI.")

    if isinstance(res.get("executive_notes"), list):
        res["executive_notes"] = "\n".join([f"• {item}" if not str(item).startswith("•") else str(item) for item in res["executive_notes"]])
    elif not res.get("executive_notes"):
        res["executive_notes"] = f"• Concern: {res['primary_concern']}\n• Action: {res['action_required']}"

    # Persist to PostgreSQL database
    try:
        with sync_engine.connect() as conn:
            conn.execute(
                text("""
                    UPDATE calls 
                    SET summary = :summary,
                        primary_concern = :concern,
                        caller_sentiment = :sentiment,
                        action_required = :action,
                        executive_notes = :notes
                    WHERE call_id = :cid
                """),
                {
                    "summary": res["summary"],
                    "concern": res["primary_concern"],
                    "sentiment": res["caller_sentiment"],
                    "action": res["action_required"],
                    "notes": res["executive_notes"],
                    "cid": call_id
                }
            )

            # Auto-register or update Lead in real-time
            call_info = conn.execute(
                text("SELECT tenant_id, caller_number, start_time, duration FROM calls WHERE call_id = :cid"),
                {"cid": call_id}
            ).fetchone()

            if call_info and call_info[1] and call_info[1] not in ('', 'unknown', 'anonymous', 'web-simulator'):
                c_tid = call_info[0] or tenant_id
                c_phone = call_info[1]
                c_start = call_info[2] or datetime.utcnow()
                c_dur = float(call_info[3] or 0.0)

                clean_phone = c_phone.lstrip("0") if c_phone.startswith("0") else c_phone
                if not clean_phone.startswith("+"):
                    clean_phone = "+91" + clean_phone if len(clean_phone) == 10 else "+" + clean_phone

                caller_name = f"Caller ({clean_phone[-4:]})"
                if res.get("summary"):
                    name_match = re.search(r"caller[,\s]+([A-Z][a-z]+ [A-Z][a-z]+)", res["summary"])
                    if name_match:
                        caller_name = name_match.group(1)

                act_lower = (res.get("action_required") or "").lower()
                sent_lower = (res.get("caller_sentiment") or "").lower()
                conc_lower = (res.get("primary_concern") or "").lower()
                sum_lower = (res.get("summary") or "").lower()

                if "callback" in act_lower or "transfer" in act_lower or "callback" in conc_lower or outcome == "transferred":
                    lead_status = "callback"
                elif "brochure" in conc_lower or "brochure" in sum_lower or "fees" in conc_lower or "interest" in sent_lower or c_dur > 35:
                    lead_status = "hot"
                elif c_dur < 8:
                    lead_status = "cold"
                else:
                    lead_status = "warm"

                lead_reason = res.get("primary_concern") or res.get("summary") or "Inbound inquiry"
                if res.get("action_required") and ("callback" in act_lower or "follow" in act_lower):
                    lead_reason = f"{lead_reason} | Action: {res['action_required']}"

                existing_lead = conn.execute(
                    text("SELECT lead_id FROM leads WHERE call_id = :cid"),
                    {"cid": call_id}
                ).fetchone()

                if existing_lead:
                    conn.execute(
                        text("""
                            UPDATE leads
                            SET reason = :reason,
                                status = :status,
                                name = CASE WHEN name LIKE 'Caller%' THEN :name ELSE name END
                            WHERE call_id = :cid
                        """),
                        {"reason": lead_reason, "status": lead_status, "name": caller_name, "cid": call_id}
                    )
                else:
                    conn.execute(
                        text("""
                            INSERT INTO leads (lead_id, tenant_id, call_id, name, phone, reason, status, captured_at)
                            VALUES (:lid, :tid, :cid, :name, :phone, :reason, :status, :captured)
                        """),
                        {
                            "lid": uuid.uuid4().hex,
                            "tid": c_tid,
                            "cid": call_id,
                            "name": caller_name,
                            "phone": clean_phone,
                            "reason": lead_reason,
                            "status": lead_status,
                            "captured": c_start
                        }
                    )

            conn.commit()
            print(f"[Executive Summary] Generated and stored for Call {call_id}: Concern='{res['primary_concern']}'")
    except Exception as e:
        print(f"[Summary Persist Error] {e}")

    return res
