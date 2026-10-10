"""
Call Logger & Telemetry Service
Non-blocking background thread persistence for call events, transcripts, and leads.
"""
import uuid
import concurrent.futures
from datetime import datetime
from typing import Optional, Dict, Any, List
from sqlalchemy import text
from app.config import settings
from app.db.session import sync_engine

# Dedicated background thread pool for zero-latency non-blocking DB operations
_DB_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="voice_db_logger")


def submit_bg_db(func, *args, **kwargs):
    """Submits a DB operation to run asynchronously in background without blocking audio thread."""
    try:
        _DB_EXECUTOR.submit(func, *args, **kwargs)
    except Exception as e:
        print(f"[DB Executor Error] {e}")


def log_call_start(call_id: str, tenant_id: str, caller_number: str, agent_id: Optional[str] = None):
    def _do():
        try:
            with sync_engine.connect() as conn:
                # Ensure tenant exists to satisfy foreign key constraint
                b_name = "iDigitalStudies" if tenant_id == "idigitalstudies" else (
                    "Phono Tel" if tenant_id == "phonotel" else f"Tenant ({tenant_id})"
                )
                conn.execute(
                    text("""
                        INSERT INTO tenants (tenant_id, business_name, status)
                        VALUES (:tid, :bname, 'active')
                        ON CONFLICT (tenant_id) DO NOTHING
                    """),
                    {"tid": tenant_id, "bname": b_name}
                )
                conn.execute(
                    text("""
                        INSERT INTO calls (call_id, tenant_id, agent_id, caller_number, start_time) 
                        VALUES (:cid, :tid, :aid, :caller, :now) 
                        ON CONFLICT (call_id) DO UPDATE SET 
                            agent_id = COALESCE(EXCLUDED.agent_id, calls.agent_id),
                            caller_number = CASE 
                                WHEN EXCLUDED.caller_number IS NOT NULL AND EXCLUDED.caller_number != 'unknown' AND EXCLUDED.caller_number != '' 
                                THEN EXCLUDED.caller_number 
                                ELSE COALESCE(calls.caller_number, EXCLUDED.caller_number) 
                            END,
                            tenant_id = COALESCE(calls.tenant_id, EXCLUDED.tenant_id)
                    """),
                    {"cid": call_id, "tid": tenant_id, "aid": agent_id, "caller": caller_number, "now": datetime.utcnow()}
                )
                conn.commit()
        except Exception as e:
            print(f"[Call Log Error] {e}")
    submit_bg_db(_do)


def log_raw_operational_cost(conn, call_id: str, dur_sec: float, outcome_tag: str):
    """
    Computes and logs the exact provider-level raw operational cost of the call in real time.
    Tracks:
    - Carrier Airtime: ₹0.00/min (RP Digital unlimited free inbound/outbound airtime plan)
    - TTS Characters used & Cost:
        * Edge-TTS: ₹0.00 (100% Free)
        * Sarvam AI bulbul:v3: ₹30 per 10k chars (₹0.0030/char)
        * Deepgram Aura: $0.030 per 1k chars (~₹0.0026/char)
    - STT Audio Seconds & Cost:
        * Sarvam AI saaras:v3: ₹30/hr = ₹0.50/min audio (₹0.00833/sec)
        * Deepgram Nova-2: $0.0043/min ≈ ₹0.37/min audio (₹0.0062/sec)
    - LLM Token Usage & Cost (Groq Llama-3.3-70b / Qwen):
        * $0.59/1M prompt tokens (₹0.000051/token), $0.79/1M completion (₹0.000068/token)
    """
    try:
        from app.core.session_store import get_session
        sess = get_session(call_id) or {}
        
        # 1. Fetch transcripts to compute ground-truth speech statistics
        transcripts = conn.execute(
            text("SELECT speaker, text FROM call_transcripts WHERE call_id = :cid ORDER BY turn_index"),
            {"cid": call_id}
        ).fetchall()

        agent_texts = [t[1] for t in transcripts if t[0] == 'agent']
        caller_texts = [t[1] for t in transcripts if t[0] == 'caller']

        # Character count: take session tracked count if available, else sum from transcripts
        db_tts_chars = sum(len(t) for t in agent_texts)
        session_tts_chars = int(sess.get("tts_characters") or 0)
        session_billable_chars = int(sess.get("tts_billable_characters") or 0)
        session_cached_chars = int(sess.get("tts_cached_characters") or 0)

        tts_chars = max(db_tts_chars, session_tts_chars)
        # Use billable characters for billing calculations (RAM cache hits are ₹0.00)
        if session_billable_chars > 0 or session_cached_chars > 0:
            billable_tts_chars = session_billable_chars if session_billable_chars > 0 else max(0, tts_chars - session_cached_chars)
        else:
            billable_tts_chars = tts_chars

        caller_chars = sum(len(t) for t in caller_texts)
        agent_words = sum(len(t.split()) for t in agent_texts)
        caller_words = sum(len(t.split()) for t in caller_texts)

        # 2. TTS Operational Cost (Dynamically based on active TTS engine)
        tts_engine = getattr(settings, "TTS_ENGINE", "sarvam").lower()
        if tts_engine in ("edge-tts", "edgetts", "microsoft"):
            tts_cost = 0.0
            tts_desc = "Microsoft Edge-TTS Neural (Free ₹0.00)"
        elif tts_engine == "deepgram":
            tts_cost = billable_tts_chars * 0.0026
            tts_desc = "Deepgram Aura ($0.030/1k chars)"
        else:
            # Sarvam AI bulbul:v3 list price: ₹30 per 10,000 characters (₹0.0030/char) on billable characters
            tts_cost = billable_tts_chars * 0.0030
            tts_desc = "Sarvam AI bulbul:v3 (₹30/10k chars)"

        # 3. STT Operational Cost (Dynamically based on active STT engine)
        stt_engine = getattr(settings, "STT_ENGINE", "sarvam").lower()
        session_stt_sec = float(sess.get("stt_audio_seconds") or 0.0)
        estimated_stt_sec = min(dur_sec * 0.5, max(len(caller_texts) * 3.5, caller_words * 0.45))
        stt_seconds = max(session_stt_sec, estimated_stt_sec)
        
        if stt_engine == "deepgram":
            stt_cost = (stt_seconds / 60.0) * 0.372
            stt_desc = "Deepgram Nova-2 ($0.0043/min)"
        else:
            # Sarvam AI saaras:v3 rate: ₹30/hour of audio = ₹0.50/min audio
            stt_cost = (stt_seconds / 60.0) * 0.50
            stt_desc = "Sarvam AI saaras:v3 (₹30/hr = ₹0.50/min)"

        # 4. LLM Operational Cost (Groq Llama-3.3-70b: $0.59/1M prompt, $0.79/1M completion @ 86.5 INR/USD)
        in_tokens = int(len(caller_texts) * 450 + sum(len(t.split()) for t in caller_texts) * 1.3)
        out_tokens = int(agent_words * 1.3)
        llm_cost = (in_tokens * 0.000051) + (out_tokens * 0.000068)

        # 5. Carrier Airtime Wholesale Cost
        # RP Digital provides unlimited free inbound and outbound airtime (fixed flat DID fee only)
        trunk_cost = 0.0
        trunk_desc = "RP Digital SIP (Unlimited Free Airtime - ₹0.00/min)"

        total_op_cost = tts_cost + stt_cost + llm_cost + trunk_cost
        dur_mins = round(dur_sec / 60.0, 2)
        cost_per_min = round(total_op_cost / (dur_sec / 60.0), 2) if dur_sec > 0 else total_op_cost

        if cost_per_min <= 1.80:
            budget_status = f"✅ OPTIMAL QUALITY TIER (₹{cost_per_min:.2f} <= ₹1.80/min target)"
        elif cost_per_min <= 2.00:
            budget_status = f"🟡 BUFFER ZONE (₹{cost_per_min:.2f} / min, within ₹0.20 safety buffer)"
        else:
            budget_status = f"❌ EXCEEDED (₹{cost_per_min:.2f} / min > ₹2.00 cap)"

        audit_log = f"""
================================================================================
📊 [CALL OPERATIONAL USAGE & RAW COST AUDIT]
   Call ID: {call_id}
   Duration: {dur_sec:.1f}s ({dur_mins:.2f} mins) | Outcome: {outcome_tag}
   -----------------------------------------------------------------------------
   🔤 Character Usage (TTS):       {tts_chars:,} characters ({billable_tts_chars:,} billable / {session_cached_chars:,} RAM cached, {agent_words} words across {len(agent_texts)} turns)
   🎙️ Caller Audio (STT):          {stt_seconds:.1f}s audio ({caller_chars:,} chars across {len(caller_texts)} turns)
   🤖 Groq LLM Token Usage:        {in_tokens + out_tokens:,} tokens ({in_tokens} prompt / {out_tokens} completion)
   -----------------------------------------------------------------------------
   💵 Pure Operational Cost Breakdown (Actual Provider Rates - 0% Markup):
      • TTS ({tts_desc}):               ₹{tts_cost:.4f}
      • STT ({stt_desc}):               ₹{stt_cost:.4f}  ({stt_seconds:.1f}s audio)
      • LLM (Groq Llama/Qwen):          ₹{llm_cost:.4f}  ({in_tokens + out_tokens:,} tokens)
      • Carrier Airtime ({trunk_desc}): ₹{trunk_cost:.4f}
   -----------------------------------------------------------------------------
   💰 TOTAL PURE OPERATIONAL COST:      ₹{total_op_cost:.4f} (approx ₹{total_op_cost:.2f})
   ⏱️ COST PER MINUTE RUN-RATE:         ₹{cost_per_min:.2f} / min | {budget_status}
================================================================================
"""
        print(audit_log)

        # Persist cost summary into executive_notes
        conn.execute(
            text("""
                UPDATE calls 
                SET executive_notes = COALESCE(executive_notes, '') || :audit
                WHERE call_id = :cid
            """),
            {"audit": f"\n[Cost Audit] TTS Chars: {tts_chars:,} | Pure Op Cost: ₹{total_op_cost:.4f}", "cid": call_id}
        )
        conn.commit()
    except Exception as e:
        print(f"[Operational Cost Audit Error] {e}")


def log_call_end(call_id: str, outcome_tag: str, transferred: bool):
    def _do():
        now = datetime.utcnow()
        try:
            with sync_engine.connect() as conn:
                # 1. Update call end time and calculate duration
                conn.execute(
                    text("""
                        UPDATE calls 
                        SET end_time = :now, 
                            duration = GREATEST(0.0, EXTRACT(EPOCH FROM (:now - start_time))),
                            outcome_tag = :tag, 
                            transferred = :tr 
                        WHERE call_id = :cid
                    """),
                    {"now": now, "tag": outcome_tag, "tr": transferred, "cid": call_id}
                )
                conn.commit()

                # 2. Settle Call Billing & Margins
                call_info = conn.execute(
                    text("""
                        SELECT c.duration, c.tenant_id, t.reseller_id, t.rate_per_min, r.wholesale_rate_per_min
                        FROM calls c
                        JOIN tenants t ON t.tenant_id = c.tenant_id
                        LEFT JOIN resellers r ON r.reseller_id = t.reseller_id
                        WHERE c.call_id = :cid
                    """),
                    {"cid": call_id}
                ).fetchone()

                if call_info:
                    dur_sec = float(call_info[0] or 0.0)
                    tenant_id = call_info[1]
                    reseller_id = call_info[2]
                    rate_per_min = float(call_info[3] or 5.00)
                    wholesale_rate = float(call_info[4] or 3.00)
                    base_cost_rate = float(getattr(settings, "PLATFORM_BASE_COST_PER_MIN", 2.00))

                    # Billable minutes (minimum 15s billable as 0.25 min, otherwise exact float)
                    billable_mins = max(0.25, round(dur_sec / 60.0, 2)) if dur_sec > 5 else 0.0

                    if billable_mins > 0:
                        client_charge = round(billable_mins * rate_per_min, 2)

                        if reseller_id:
                            reseller_cost = round(billable_mins * wholesale_rate, 2)
                            reseller_profit = round(max(0.0, client_charge - reseller_cost), 2)
                            owner_profit = round(max(0.0, reseller_cost - (billable_mins * base_cost_rate)), 2)

                            # Deduct from client wallet
                            conn.execute(
                                text("UPDATE tenants SET wallet_balance = GREATEST(0.0, wallet_balance - :chg) WHERE tenant_id = :tid"),
                                {"chg": client_charge, "tid": tenant_id}
                            )
                            # Deduct wholesale cost from reseller wallet
                            conn.execute(
                                text("UPDATE resellers SET wallet_balance = GREATEST(0.0, wallet_balance - :cst) WHERE reseller_id = :rid"),
                                {"cst": reseller_cost, "rid": reseller_id}
                            )
                        else:
                            # Direct business (no reseller)
                            reseller_cost = 0.0
                            reseller_profit = 0.0
                            owner_profit = round(max(0.0, client_charge - (billable_mins * base_cost_rate)), 2)

                            # Deduct from client wallet
                            conn.execute(
                                text("UPDATE tenants SET wallet_balance = GREATEST(0.0, wallet_balance - :chg) WHERE tenant_id = :tid"),
                                {"chg": client_charge, "tid": tenant_id}
                            )

                        # Insert billing transaction record
                        tx_id = f"tx_{uuid.uuid4().hex[:12]}"
                        conn.execute(text("""
                            INSERT INTO billing_transactions (
                                transaction_id, call_id, tenant_id, reseller_id, duration_seconds, 
                                billable_minutes, rate_per_min, wholesale_rate_per_min, client_charged, 
                                reseller_cost, reseller_profit, owner_net_profit, notes, timestamp
                            ) VALUES (
                                :tx, :cid, :tid, :rid, :dur, :bm, :rpm, :wrpm, :cc, :rc, :rp, :op, 'Call Airtime Settle', :now
                            )
                        """), {
                            "tx": tx_id, "cid": call_id, "tid": tenant_id, "rid": reseller_id,
                            "dur": dur_sec, "bm": billable_mins, "rpm": rate_per_min, "wrpm": wholesale_rate,
                            "cc": client_charge, "rc": reseller_cost, "rp": reseller_profit, "op": owner_profit,
                            "now": now
                        })
                        conn.commit()
                        print(f"💰 [Billing Settle] Call {call_id}: {dur_sec}s ({billable_mins}m) -> Client: ₹{client_charge} | Reseller Profit: ₹{reseller_profit} | Owner Net Profit: ₹{owner_profit}")

                # 3. Log Pure Provider-Level Operational Cost & Character Usage Audit
                log_raw_operational_cost(conn, call_id, dur_sec, outcome_tag)
        except Exception as e:
            print(f"[Call End DB/Billing Error] {e}")
        try:
            from app.core.summarizer import generate_executive_summary
            generate_executive_summary(call_id=call_id, outcome=outcome_tag)
        except Exception as e:
            print(f"[Auto-Summary Error on Call End] {e}")
    submit_bg_db(_do)


def log_transcript(call_id: str, tenant_id: str, turn_index: int, speaker: str, text_content: str):
    def _do():
        try:
            transcript_id = uuid.uuid4().hex
            with sync_engine.connect() as conn:
                conn.execute(
                    text("INSERT INTO calls (call_id, tenant_id, start_time) VALUES (:cid, :tid, :now) ON CONFLICT (call_id) DO NOTHING"),
                    {"cid": call_id, "tid": tenant_id, "now": datetime.utcnow()}
                )
                conn.execute(
                    text("INSERT INTO call_transcripts (transcript_id, call_id, tenant_id, turn_index, speaker, text) VALUES (:tid_pk, :cid, :tid, :ti, :sp, :txt)"),
                    {"tid_pk": transcript_id, "cid": call_id, "tid": tenant_id, "ti": turn_index, "sp": speaker, "txt": text_content}
                )
                conn.commit()
        except Exception as e:
            print(f"[Transcript Log Error] {e}")
    submit_bg_db(_do)


def log_lead(tenant_id: str, call_id: str, name: str, phone: str, reason: str):
    def _do():
        try:
            lead_id = uuid.uuid4().hex
            with sync_engine.connect() as conn:
                conn.execute(
                    text("INSERT INTO leads (lead_id, tenant_id, call_id, name, phone, reason) VALUES (:lid, :tid, :cid, :name, :phone, :reason)"),
                    {"lid": lead_id, "tid": tenant_id, "cid": call_id, "name": name, "phone": phone, "reason": reason}
                )
                conn.commit()
        except Exception as e:
            print(f"[Lead Log Error] {e}")
    submit_bg_db(_do)
