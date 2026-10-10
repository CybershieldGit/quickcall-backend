"""
Enterprise Messaging & Brochure Dispatch Service
Supports:
- Multi-channel delivery: WhatsApp (Meta Cloud API, Twilio, Mock) and Email (SMTP, SendGrid, Mock)
- Spoken-to-contact extraction: handles conversational speech for phone numbers and email addresses
- Speech-friendly formatting for clear TTS confirmation playback (digit pacing and phonetic email readout)
- Tenant-aware brochure assets, customized templates, and delivery logging
"""
import re
import os
import json
import logging
import asyncio
from typing import Optional, Dict, Any, Tuple
import httpx

from app.config import settings
from app.core.tenant_service import load_tenant_config, normalize_tenant_id

logger = logging.getLogger("qwickcall.messaging")

# -----------------------------------------------------------------------------
# Spoken to Contact Parsing Utilities
# -----------------------------------------------------------------------------
WORD_TO_DIGIT = {
    # English
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    # Hindi / Hinglish phonetics
    "shunya": "0", "sunya": "0", "ek": "1", "do": "2", "teen": "3", "tin": "3",
    "chaar": "4", "char": "4", "paanch": "5", "panch": "5", "chhe": "6", "che": "6",
    "saat": "7", "sat": "7", "aath": "8", "ath": "8", "nau": "9", "no": "9"
}

def extract_phone_number(text: str) -> Optional[str]:
    """
    Extracts a 10-15 digit phone number from raw user speech or text.
    Handles:
    - Pure digit strings: "9876543210", "+91 98765 43210"
    - Spoken numbers: "nine eight seven six five four three two one zero"
    - Mixed spoken digits: "double nine eight seven..."
    """
    if not text:
        return None

    t_clean = text.lower().replace("-", " ").replace(".", " ")
    
    # 1. Expand spoken multipliers (double, triple)
    tokens = t_clean.split()
    expanded_tokens = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("double", "dabal") and i + 1 < len(tokens):
            next_tok = tokens[i + 1]
            expanded_tokens.extend([next_tok, next_tok])
            i += 2
            continue
        elif tok in ("triple", "tripple") and i + 1 < len(tokens):
            next_tok = tokens[i + 1]
            expanded_tokens.extend([next_tok, next_tok, next_tok])
            i += 2
            continue
        expanded_tokens.append(tok)
        i += 1

    # 2. Map word digits to numbers
    digit_chars = []
    for tok in expanded_tokens:
        # If token has literal digits
        sub_digits = "".join([c for c in tok if c.isdigit()])
        if sub_digits:
            digit_chars.append(sub_digits)
        elif tok in WORD_TO_DIGIT:
            digit_chars.append(WORD_TO_DIGIT[tok])

    combined_digits = "".join(digit_chars)

    # 3. Look for 10-12 digit sequence
    # Match standard 10 digit Indian number or +91 / 91 prefix
    if len(combined_digits) >= 10:
        # Check from right for 10 digits if longer
        if len(combined_digits) == 10:
            return combined_digits
        elif len(combined_digits) == 12 and combined_digits.startswith("91"):
            return combined_digits[2:]
        elif len(combined_digits) == 11 and combined_digits.startswith("0"):
            return combined_digits[1:]
        
        # Regex search for 10 digits
        m = re.search(r'([6-9]\d{9})', combined_digits)
        if m:
            return m.group(1)
        # Any continuous 10 digits
        m10 = re.search(r'(\d{10})', combined_digits)
        if m10:
            return m10.group(1)

    # Direct regex over original text (e.g. "+919876543210")
    m_raw = re.search(r'(?:\+?91[\-\s]?)?([6-9]\d{9})\b', text)
    if m_raw:
        return m_raw.group(1)

    return None


SPOKEN_DIGITS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"
}

POPULAR_EMAIL_DOMAINS = {
    "gmail": "gmail.com",
    "yahoo": "yahoo.com",
    "outlook": "outlook.com",
    "hotmail": "hotmail.com",
    "icloud": "icloud.com",
    "rediffmail": "rediffmail.com",
}

EMAIL_PREFIX_PATTERNS = [
    r"^.*?\b(?:no\s*,?\s*)?(?:i\s+will\s+give\s+you\s+my|i\s+will\s+share\s+my|let\s+me\s+give\s+you|giving\s+you)\s+(?:correct\s+|new\s+)?(?:email\s+address|email\s+id|mail\s+id|email|mail)\s*(?:is|hai)?[:\s\.]*",
    r"^.*?\b(?:email\s+address|email\s+id|mail\s+id|email|mail)\s+(?:is|hai)?[:\s\.]*",
    r"^.*?\b(?:my|mera|apna|correct|sahi|new)\s+(?:email\s+address|email\s+id|mail\s+id|email|mail)\s*(?:is|hai)?[:\s\.]*",
    r"^.*?\b(?:send\s+(?:it\s+)?(?:to|on)|bhejo|bhej\s+do|note\s+down|note\s+kar\s+lo|write\s+down)\b[:\s\.]*",
    r"^.*?\b(?:it\s+is|this\s+is|it'\''s|yeh\s+hai)\b[:\s\.]*",
    r"^.*?\b(?:no\s+wait|wait\s+no|actually)\b[:\s\.]*",
]


def extract_email_address(text: str) -> Optional[str]:
    """
    Extracts an email address from text or spoken speech.
    Robustly handles:
    - Standard email formats: "arpan.sharma@example.com"
    - STT output with spaces in names: "Arpan Sharma917@gmail.com." -> "arpansharma917@gmail.com"
    - Spoken numbers & symbols: "arpan dot sharma nine one seven at gmail dot com"
    - Conversational prefixes: "No, my email is arpan sharma 9 1 7 @ gmail.com"
    - Missing '.com' for common providers: "arpan917 at gmail" -> "arpan917@gmail.com"
    """
    if not text:
        return None

    # Step 1: Normalize spoken speech tokens
    t = text.lower().strip()
    t = re.sub(r'\bat\s+the\s+rate(?:\s+of)?\b', '@', t)
    t = re.sub(r'\bat\s+rate\b', '@', t)
    t = re.sub(r'\b(?:at|@)\b', '@', t)
    t = re.sub(r'\b(?:dot|point)\b', '.', t)
    t = re.sub(r'\bunderscore\b', '_', t)
    t = re.sub(r'\b(?:dash|hyphen)\b', '-', t)

    for word, digit in SPOKEN_DIGITS.items():
        t = re.sub(rf'\b{word}\b', digit, t)

    # Step 2: If @ is present (either directly or converted from spoken tokens)
    if "@" in t:
        parts = t.split("@")
        # In case user said multiple @ tokens, the last one is the domain boundary
        local_raw = "@".join(parts[:-1]).strip()
        domain_raw = parts[-1].strip()

        # Strip conversational prefix phrases from local part
        for pat in EMAIL_PREFIX_PATTERNS:
            local_raw = re.sub(pat, "", local_raw, flags=re.IGNORECASE).strip()

        # Strip any leading non-alphanumeric punctuation (. , - /)
        local_raw = re.sub(r"^[^a-z0-9]+", "", local_raw).strip()

        # In email handles, spaces between words/digits should be removed
        # (e.g. STT output "Arpan Sharma917" or "arpan sharma 9 1 7" -> "arpansharma917")
        local_clean = re.sub(r'\s+', '', local_raw)
        # Keep only valid email username characters
        local_clean = re.sub(r'[^a-z0-9._+-]', '', local_clean).strip("._+-")

        # Parse domain
        domain_clean = re.sub(r'\s*\.\s*', '.', domain_raw)
        m_dom = re.search(r'\b([a-z0-9-]+(?:\.[a-z]{2,})+)\b', domain_clean)
        domain = None
        if m_dom:
            domain = m_dom.group(1).rstrip(".")
        else:
            first_word = domain_clean.split()[0].strip() if domain_clean else ""
            if first_word in POPULAR_EMAIL_DOMAINS:
                domain = POPULAR_EMAIL_DOMAINS[first_word]

        if local_clean and domain:
            return f"{local_clean}@{domain}"

    # Step 3: Direct standard email regex fallback
    m = re.search(r'([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)', text)
    if m:
        return m.group(1).lower().strip().rstrip(".")

    return None


def format_phone_for_speech(phone: str) -> str:
    """
    Formats phone digits with spaces so TTS synthesizers read each digit separately
    with a natural conversational cadence rather than a single gigantic integer.
    Example: '9876543210' -> '9 8 7 6 5, 4 3 2 1 0'
    """
    clean_digits = "".join([c for c in phone if c.isdigit()])
    if len(clean_digits) == 10:
        first_half = " ".join(clean_digits[:5])
        second_half = " ".join(clean_digits[5:])
        return f"{first_half}, {second_half}"
    elif len(clean_digits) > 10:
        return " ".join(clean_digits)
    return " ".join(phone)


def format_email_for_speech(email: str) -> str:
    """
    Formats email into spoken words for clear TTS verification.
    Example: 'rahul.s@gmail.com' -> 'rahul dot s at gmail dot com'
    """
    if not email:
        return ""
    spoken = email.lower()
    spoken = spoken.replace("@", " at ")
    spoken = spoken.replace(".", " dot ")
    spoken = spoken.replace("_", " underscore ")
    spoken = spoken.replace("-", " dash ")
    return re.sub(r'\s+', ' ', spoken).strip()


# -----------------------------------------------------------------------------
# Delivery Provider Implementations
# -----------------------------------------------------------------------------
async def send_whatsapp_message(
    to_number: str,
    message: str,
    media_url: Optional[str] = None,
    media_filename: Optional[str] = None,
    tenant_id: str = "idigitalstudies"
) -> Dict[str, Any]:
    """
    Sends a WhatsApp message via Meta Cloud API, Twilio WhatsApp, or Mock Logger.
    """
    provider = (settings.WHATSAPP_PROVIDER or "mock").lower()
    clean_number = "".join([c for c in to_number if c.isdigit()])
    if len(clean_number) == 10:
        clean_number = f"91{clean_number}"
    
    formatted_dest = f"+{clean_number}"

    # 1. Meta WhatsApp Business Cloud API
    if provider == "meta" and settings.WHATSAPP_META_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID:
        url = f"https://graph.facebook.com/v18.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {settings.WHATSAPP_META_TOKEN}",
            "Content-Type": "application/json"
        }
        payload: Dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": clean_number
        }
        if media_url:
            payload["type"] = "document"
            payload["document"] = {
                "link": media_url,
                "caption": message,
                "filename": media_filename or "Course_Brochure.pdf"
            }
        else:
            payload["type"] = "text"
            payload["text"] = {"preview_url": True, "body": message}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, headers=headers, json=payload)
                if res.status_code in (200, 201):
                    data = res.json()
                    msg_id = data.get("messages", [{}])[0].get("id", "meta_ok")
                    logger.info(f"✅ [WhatsApp Meta API] Successfully dispatched to {formatted_dest} (ID: {msg_id})")
                    return {"status": "delivered", "provider": "meta", "channel": "whatsapp", "message_id": msg_id, "destination": formatted_dest}
                else:
                    logger.error(f"❌ [WhatsApp Meta API Error] {res.status_code}: {res.text}")
        except Exception as e:
            logger.error(f"❌ [WhatsApp Meta Exception] {e}")

    # 2. Twilio WhatsApp API
    elif provider == "twilio" and settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN:
        twilio_url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json"
        auth = (settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        from_num = settings.TWILIO_WHATSAPP_FROM
        if not from_num.startswith("whatsapp:"):
            from_num = f"whatsapp:{from_num}"

        data_payload = {
            "From": from_num,
            "To": f"whatsapp:{formatted_dest}",
            "Body": message
        }
        if media_url:
            data_payload["MediaUrl"] = media_url

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(twilio_url, auth=auth, data=data_payload)
                if res.status_code in (200, 201):
                    msg_id = res.json().get("sid", "twilio_ok")
                    logger.info(f"✅ [WhatsApp Twilio API] Successfully dispatched to {formatted_dest} (SID: {msg_id})")
                    return {"status": "delivered", "provider": "twilio", "channel": "whatsapp", "message_id": msg_id, "destination": formatted_dest}
                else:
                    logger.error(f"❌ [WhatsApp Twilio Error] {res.status_code}: {res.text}")
        except Exception as e:
            logger.error(f"❌ [WhatsApp Twilio Exception] {e}")

    # 3. Mock Dispatch Simulator (Default / Fallback)
    print("\n" + "=" * 65)
    print(f"📱 [WHATSAPP DISPATCH SIMULATOR]")
    print(f"• Tenant      : {tenant_id}")
    print(f"• Destination : {formatted_dest}")
    print(f"• Media URL   : {media_url or 'None'}")
    print(f"• Message Content:\n{message}")
    print("=" * 65 + "\n")
    logger.info(f"🟢 [WhatsApp Mock] Simulated delivery to {formatted_dest}")
    return {
        "status": "delivered",
        "provider": "mock",
        "channel": "whatsapp",
        "destination": formatted_dest,
        "note": "Delivered via Mock WhatsApp Dispatcher"
    }


async def send_email_message(
    to_email: str,
    subject: str,
    body_text: str,
    body_html: Optional[str] = None,
    attachment_url: Optional[str] = None,
    tenant_id: str = "idigitalstudies"
) -> Dict[str, Any]:
    """
    Sends an Email via SMTP, SendGrid, or Mock Logger.
    """
    provider = (settings.EMAIL_PROVIDER or "mock").lower()

    # 1. SendGrid API
    if provider == "sendgrid" and settings.SENDGRID_API_KEY:
        url = "https://api.sendgrid.com/v3/mail/send"
        headers = {
            "Authorization": f"Bearer {settings.SENDGRID_API_KEY}",
            "Content-Type": "application/json"
        }
        content_list = [{"type": "text/plain", "value": body_text}]
        if body_html:
            content_list.append({"type": "text/html", "value": body_html})

        payload = {
            "personalizations": [{"to": [{"email": to_email}]}],
            "from": {"email": settings.SENDGRID_FROM_EMAIL or "admissions@idigitalstudies.com"},
            "subject": subject,
            "content": content_list
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, headers=headers, json=payload)
                if res.status_code in (200, 202):
                    logger.info(f"✅ [SendGrid Email] Successfully dispatched to {to_email}")
                    return {"status": "delivered", "provider": "sendgrid", "channel": "email", "destination": to_email}
                else:
                    logger.error(f"❌ [SendGrid Error] {res.status_code}: {res.text}")
        except Exception as e:
            logger.error(f"❌ [SendGrid Exception] {e}")

    # 2. SMTP Provider
    elif provider == "smtp" and settings.SMTP_HOST and settings.SMTP_USER and settings.SMTP_PASSWORD:
        def _send_smtp_sync():
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = settings.SMTP_FROM_EMAIL or settings.SMTP_USER
            msg["To"] = to_email

            part1 = MIMEText(body_text, "plain")
            msg.attach(part1)
            if body_html:
                part2 = MIMEText(body_html, "html")
                msg.attach(part2)

            clean_pwd = (settings.SMTP_PASSWORD or "").replace(" ", "").strip()
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(settings.SMTP_USER, clean_pwd)
                server.sendmail(msg["From"], [to_email], msg.as_string())

        try:
            await asyncio.to_thread(_send_smtp_sync)
            logger.info(f"✅ [SMTP Email] Successfully dispatched to {to_email}")
            return {"status": "delivered", "provider": "smtp", "channel": "email", "destination": to_email}
        except Exception as e:
            logger.error(f"❌ [SMTP Exception] Failed to deliver email via SMTP: {e}")
            logger.warning("💡 [SMTP Diagnosis] If using Gmail, 'Connection unexpectedly closed' occurs when Google rejects your password (535 5.7.8 BadCredentials). Make sure you are using a dedicated 16-character Google App Password (https://myaccount.google.com/apppasswords) with 2FA enabled on the account.")

    # 3. Mock Email Simulator (Default / Fallback)
    print("\n" + "=" * 65)
    print(f"📧 [EMAIL DISPATCH SIMULATOR]")
    print(f"• Tenant      : {tenant_id}")
    print(f"• To          : {to_email}")
    print(f"• Subject     : {subject}")
    print(f"• Attachment  : {attachment_url or 'None'}")
    print(f"• Body:\n{body_text}")
    print("=" * 65 + "\n")
    logger.info(f"🟢 [Email Mock] Simulated delivery to {to_email}")
    return {
        "status": "simulated" if provider == "smtp" else "delivered",
        "provider": "mock",
        "channel": "email",
        "destination": to_email,
        "note": "Delivered via Mock Email Dispatcher (Real SMTP failed or unconfigured)" if provider == "smtp" else "Delivered via Mock Email Dispatcher"
    }


# -----------------------------------------------------------------------------
# High-Level Brochure Dispatch Orchestrator
# -----------------------------------------------------------------------------
async def send_brochure(
    tenant_id: str,
    channel: str,
    destination: str,
    customer_name: Optional[str] = None,
    notes: Optional[str] = None
) -> Dict[str, Any]:
    """
    Dispatches tenant brochure to the caller via WhatsApp or Email.
    - Loads tenant-specific brochure URL and metadata
    - Personalizes message copy
    - Calls corresponding channel transport
    - Logs delivery event for CRM / Lead tracking
    """
    tid = normalize_tenant_id(tenant_id)
    cfg = load_tenant_config(tid) or {}
    rules = cfg.get("business_rules", {})

    b_name = cfg.get("business_name") or ("iDigitalStudies" if "digital" in tid else "Customer Support")
    advisor = rules.get("agent_name") or ("Aanya" if "digital" in tid else "Rohan")
    
    brochure_url = (
        rules.get("brochure_url")
        or getattr(settings, "DEFAULT_BROCHURE_URL", "https://idigitalstudies.com/assets/brochure.pdf")
    )
    brochure_title = rules.get("brochure_title") or f"{b_name} Official Curriculum & Admissions Brochure"
    
    norm_channel = channel.lower().strip()
    c_name = customer_name or "Student"

    if "what" in norm_channel or norm_channel == "wa":
        # Build WhatsApp Message
        wa_message = (
            f"Hello {c_name}! 👋\n\n"
            f"Thank you for contacting *{b_name}*. As requested during your call with {advisor}, "
            f"here is our official *{brochure_title}*:\n\n"
            f"📄 *Download Brochure:* {brochure_url}\n\n"
            f"Key Highlights:\n"
            f"• 100% Placement Assistance with 300+ Hiring Partners\n"
            f"• Live Hands-On Practical Industry Projects\n"
            f"• Flexible Weekend & Weekday Batches (Online & Offline)\n\n"
            f"Feel free to reply directly to this chat if you have any questions or wish to book a campus counseling session! 🚀"
        )
        result = await send_whatsapp_message(
            to_number=destination,
            message=wa_message,
            media_url=brochure_url,
            media_filename=f"{b_name.replace(' ', '_')}_Brochure.pdf",
            tenant_id=tid
        )
        result["brochure_url"] = brochure_url
        result["business_name"] = b_name
        return result

    elif "mail" in norm_channel or norm_channel == "email":
        subject = f"Your Requested Course Brochure & Syllabus — {b_name}"
        body_text = (
            f"Dear {c_name},\n\n"
            f"Thank you for your interest in {b_name}.\n\n"
            f"As requested during your call with our advisor {advisor}, please find the details and "
            f"download link for our official course brochure below:\n\n"
            f"Brochure Link: {brochure_url}\n\n"
            f"Program Highlights:\n"
            f"- Comprehensive hands-on curriculum\n"
            f"- 100% dedicated placement support\n"
            f"- Recognized industry certifications\n\n"
            f"If you have any questions, simply reply to this email or call our admissions desk.\n\n"
            f"Warm regards,\n"
            f"{advisor} | Admissions Counselor\n"
            f"{b_name}"
        )
        body_html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e0e0e0; border-radius: 8px;">
                <h2 style="color: #2563eb;">{b_name}</h2>
                <p>Dear {c_name},</p>
                <p>Thank you for speaking with our advisor <strong>{advisor}</strong>. As requested, here is your official course information and syllabus brochure.</p>
                <div style="text-align: center; margin: 30px 0;">
                    <a href="{brochure_url}" style="background-color: #2563eb; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block;">
                        📥 Download Official Brochure (PDF)
                    </a>
                </div>
                <div style="background-color: #f8fafc; padding: 15px; border-radius: 6px; margin: 20px 0;">
                    <h4 style="margin-top: 0; color: #1e293b;">Key Program Highlights:</h4>
                    <ul>
                        <li>100% Placement Support with leading MNCs & Agencies</li>
                        <li>Hands-on Live Project Training & Certifications</li>
                        <li>Mentorship by Senior Industry Practitioners</li>
                    </ul>
                </div>
                <p>If you'd like to schedule a 1-on-1 counseling session or batch visit, simply reply to this email.</p>
                <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;" />
                <p style="font-size: 13px; color: #64748b;">
                    Warm regards,<br />
                    <strong>{advisor}</strong> | Admissions Team<br />
                    {b_name}
                </p>
            </div>
        </body>
        </html>
        """
        result = await send_email_message(
            to_email=destination,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            attachment_url=brochure_url,
            tenant_id=tid
        )
        result["brochure_url"] = brochure_url
        result["business_name"] = b_name
        return result

    else:
        logger.warning(f"Unknown dispatch channel requested: {channel}")
        return {"status": "error", "error": f"Unsupported channel: {channel}"}
