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
from app.core.secrets_box import decrypt_secret
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
    "zero": "0", "shunya": "0", "sunya": "0",
    "one": "1", "two": "2", "three": "3", "four": "4",
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
    r"^.*?\b(?:no\s*,?\s*)*(?:you\s+(?:have\s+)?(?:noted|got|heard)\s+(?:it\s+)?wrong|galat\s+(?:hai|note\s+kiya)|that\'s\s+wrong|not\s+this|wrong\s+hai)\b[:\s\.]*",
    r"^.*?\b(?:no\s*,?\s*)*(?:no\s+no|no\s*,?\s*it\'s\s+not|not\s+double\s+zero|galat\s+hai)\b[:\s\.]*",
    r"^.*?\b(?:no\s*,?\s*)?(?:i\s+will\s+give\s+you\s+my|i\s+will\s+share\s+my|let\s+me\s+give\s+you|giving\s+you)\s+(?:correct\s+|new\s+)?(?:email\s+address|email\s+id|mail\s+id|email|mail)\s*(?:is|hai)?[:\s\.]*",
    r"^.*?\b(?:email\s+address|email\s+id|mail\s+id|email|mail)\s+(?:is|hai)?[:\s\.]*",
    r"^.*?\b(?:my|mera|apna|correct|sahi|new)\s+(?:email\s+address|email\s+id|mail\s+id|email|mail)\s*(?:is|hai)?[:\s\.]*",
    r"^.*?\b(?:send\s+(?:it\s+)?(?:to|on)|bhejo|bhej\s+do|note\s+down|note\s+kar\s+lo|write\s+down)\b[:\s\.]*",
    r"^.*?\b(?:it\s+is|this\s+is|it'\''s|its|yeh\s+hai)\b[:\s\.]*",
    r"^.*?\b(?:no\s+wait|wait\s+no|actually)\b[:\s\.]*",
]


def extract_email_address(text: str) -> Optional[str]:
    """
    Extracts an email address from text or spoken speech.
    Robustly handles:
    - Standard email formats: "arpan.sharma@example.com"
    - STT output with spaces in names: "Arpan Sharma917@gmail.com." -> "arpansharma917@gmail.com"
    - Spoken numbers & symbols: "arpan dot sharma nine one seven at gmail dot com"
    - Multipliers: "Ankita Tiwari P triple zero @gmail.com" -> "ankitatiwarip000@gmail.com"
    - Conversational prefixes: "No, my email is arpan sharma 9 1 7 @ gmail.com"
    - Missing '.com' for common providers: "arpan917 at gmail" -> "arpan917@gmail.com"
    """
    if not text:
        return None

    # Step 1: Normalize spoken speech tokens
    t = text.lower().strip()
    t = re.sub(r'\bg\s*mail\b', 'gmail', t)
    t = re.sub(r'\bya\s*hoo\b', 'yahoo', t)
    t = re.sub(r'\bhot\s*mail\b', 'hotmail', t)
    t = re.sub(r'\bat\s+the\s+rate(?:\s+of)?\b', '@', t)
    t = re.sub(r'\bat\s+rate\b', '@', t)
    t = re.sub(r'\b(?:at|@)\b', '@', t)
    t = re.sub(r'\b(?:dot|point)\b', '.', t)
    t = re.sub(r'\bunderscore\b', '_', t)
    t = re.sub(r'\b(?:dash|hyphen)\b', '-', t)

    # Pad delimiters to prevent accidental token concatenation
    t = re.sub(r'([@_])', r' \1 ', t)

    # Normalize patterns like "three zeros" / "2 zeros"
    t = re.sub(r'\b(?:two|2)\s+zeros?\b', '0 0', t)
    t = re.sub(r'\b(?:three|3)\s+zeros?\b', '0 0 0', t)
    t = re.sub(r'\b(?:four|4)\s+zeros?\b', '0 0 0 0', t)

    # Expand spoken multipliers (double, triple, etc.)
    tokens = t.split()
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
        elif tok in ("quadruple",) and i + 1 < len(tokens):
            next_tok = tokens[i + 1]
            expanded_tokens.extend([next_tok, next_tok, next_tok, next_tok])
            i += 2
            continue
        expanded_tokens.append(tok)
        i += 1
    t = " ".join(expanded_tokens)

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


DIGIT_TO_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine"
}


def format_email_for_speech(email: str) -> str:
    """
    Formats email into spoken words for clear TTS verification.
    Converts numbers into spoken words so TTS engines (Deepgram Aura, Sarvam, etc.)
    pronounce each digit clearly and never swallow digits in alphanumeric handles.
    Example: 'arpansharma917@gmail.com' -> 'arpansharma nine one seven at gmail dot com'
    """
    if not email:
        return ""
    spoken = email.lower()
    spoken = spoken.replace("@", " at ")
    spoken = spoken.replace(".", " dot ")
    spoken = spoken.replace("_", " underscore ")
    spoken = spoken.replace("-", " dash ")
    spoken = spoken.replace("+", " plus ")

    parts = []
    for token in spoken.split():
        if token in ("at", "dot", "underscore", "dash", "plus", "com", "in", "org", "net", "edu", "co"):
            parts.append(token)
        else:
            # Separate letters and digits, expanding digits to spoken words
            sub_tokens = re.findall(r'[a-zA-Z]+|\d', token)
            converted = [DIGIT_TO_WORDS.get(st, st) for st in sub_tokens]
            parts.append(" ".join(converted))

    return re.sub(r'\s+', ' ', " ".join(parts)).strip()



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
    Sends an Email via SMTP, SendGrid, or Mock Logger with complete per-tenant isolation:
    - Resolves tenant branding (business name, sender name, custom reply-to)
    - Supports per-tenant BYO-SMTP credentials if configured in tenant_config
    - Falls back to platform SMTP / SendGrid with tenant-branded From display and Reply-To headers
    """
    provider = (settings.EMAIL_PROVIDER or "mock").lower()

    # Load tenant configuration
    tid = normalize_tenant_id(tenant_id)
    cfg = load_tenant_config(tid) or {}
    rules = cfg.get("business_rules", {})
    b_name = cfg.get("business_name") or ("iDigitalStudies" if "digital" in tid else "Customer Support")

    sender_name = rules.get("sender_name") or f"{b_name} Support"
    tenant_email = rules.get("sender_email") or rules.get("reply_to_email") or cfg.get("email")
    reply_to = rules.get("reply_to_email") or tenant_email

    # Check if tenant has their own custom SMTP server configured
    tenant_smtp = dict(rules.get("smtp") or {})
    if tenant_smtp.get("password"):
        tenant_smtp["password"] = decrypt_secret(tenant_smtp["password"])
    has_custom_smtp = bool(tenant_smtp.get("host") and tenant_smtp.get("user") and tenant_smtp.get("password"))

    # 1. SendGrid API
    if provider == "sendgrid" and not has_custom_smtp:
        if not settings.SENDGRID_API_KEY:
            fail_box = (
                "\n" + "=" * 65 + "\n"
                f"❌ [EMAIL DISPATCH FAILED — MISSING SENDGRID API KEY]\n"
                f"• Tenant      : {tenant_id} ({b_name})\n"
                f"• Destination : {to_email}\n"
                f"• Provider    : sendgrid\n"
                f"• Reason      : EMAIL_PROVIDER is 'sendgrid', but SENDGRID_API_KEY is not set in .env.\n"
                + "=" * 65 + "\n"
            )
            print(fail_box)
            logger.error(f"❌ [SendGrid Failed] SENDGRID_API_KEY is not configured in .env for destination: {to_email}")
            return {"status": "failed", "provider": "sendgrid", "error": "Missing SENDGRID_API_KEY in .env", "channel": "email", "destination": to_email}

        url = "https://api.sendgrid.com/v3/mail/send"
        headers = {
            "Authorization": f"Bearer {settings.SENDGRID_API_KEY}",
            "Content-Type": "application/json"
        }
        content_list = [{"type": "text/plain", "value": body_text}]
        if body_html:
            content_list.append({"type": "text/html", "value": body_html})

        from_email = rules.get("sender_email") or settings.SENDGRID_FROM_EMAIL or "admissions@idigitalstudies.com"
        payload = {
            "personalizations": [{"to": [{"email": to_email}]}],
            "from": {"email": from_email, "name": sender_name},
            "subject": subject,
            "content": content_list
        }
        if reply_to:
            payload["reply_to"] = {"email": reply_to, "name": sender_name}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, headers=headers, json=payload)
                if res.status_code in (200, 202):
                    logger.info(f"✅ [SendGrid Email] Dispatched to {to_email} (Tenant: {tid}, From: {from_email})")
                    print(f"✅ [SendGrid Email] Successfully delivered to {to_email}")
                    return {"status": "delivered", "provider": "sendgrid", "channel": "email", "destination": to_email}
                else:
                    fail_box = (
                        "\n" + "=" * 65 + "\n"
                        f"❌ [EMAIL DISPATCH FAILED — SENDGRID API ERROR]\n"
                        f"• Tenant      : {tenant_id} ({b_name})\n"
                        f"• Destination : {to_email}\n"
                        f"• HTTP Status : {res.status_code}\n"
                        f"• API Response: {res.text}\n"
                        + "=" * 65 + "\n"
                    )
                    print(fail_box)
                    logger.error(f"❌ [SendGrid Error] {res.status_code}: {res.text}")
                    return {"status": "failed", "provider": "sendgrid", "error": f"HTTP {res.status_code}: {res.text}", "channel": "email", "destination": to_email}
        except Exception as e:
            fail_box = (
                "\n" + "=" * 65 + "\n"
                f"❌ [EMAIL DISPATCH FAILED — SENDGRID EXCEPTION]\n"
                f"• Tenant      : {tenant_id} ({b_name})\n"
                f"• Destination : {to_email}\n"
                f"• Error Type  : {type(e).__name__}\n"
                f"• Error Detail: {e}\n"
                + "=" * 65 + "\n"
            )
            print(fail_box)
            logger.error(f"❌ [SendGrid Exception] {e}")
            return {"status": "failed", "provider": "sendgrid", "error": str(e), "channel": "email", "destination": to_email}

    # 2. SMTP Provider (Strict Multi-Tenant Sandbox Isolation)
    elif provider == "smtp" or has_custom_smtp:
        if not has_custom_smtp:
            msg = f"Dedicated SMTP credentials not configured for tenant '{tid}'. Please configure your SMTP server in Workspace Settings."
            logger.warning(f"❌ [Sandbox Isolation] {msg}")
            return {
                "status": "failed",
                "provider": "tenant_smtp",
                "error": msg,
                "channel": "email",
                "destination": to_email
            }

        smtp_host = tenant_smtp.get("host")
        smtp_user = tenant_smtp.get("user")
        smtp_pass = tenant_smtp.get("password")
        smtp_port = int(tenant_smtp.get("port") or 587)
        from_email = tenant_smtp.get("from_email") or smtp_user

        # Check for missing credentials
        if not (smtp_host and smtp_user and smtp_pass):
            missing_items = []
            if not smtp_host:
                missing_items.append("SMTP_HOST")
            if not smtp_user:
                missing_items.append("SMTP_USER")
            if not smtp_pass:
                missing_items.append("SMTP_PASSWORD")

            fail_box = (
                "\n" + "=" * 65 + "\n"
                f"❌ [EMAIL DISPATCH FAILED — MISSING SMTP CREDENTIALS]\n"
                f"• Tenant      : {tenant_id} ({b_name})\n"
                f"• Destination : {to_email}\n"
                f"• Config Source: {'Tenant Custom Settings' if has_custom_smtp else 'Platform .env'}\n"
                f"• Missing Info: {', '.join(missing_items)}\n"
                f"• SMTP Server : {smtp_host or '[NOT CONFIGURED]'}:{smtp_port}\n"
                f"• SMTP User   : {smtp_user or '[EMPTY]'}\n"
                f"• Reason      : Live email was requested, but no valid SMTP credentials exist.\n"
                f"• Solution    : Add SMTP_USER and SMTP_PASSWORD in .env (or configure tenant SMTP in dashboard).\n"
                + "=" * 65 + "\n"
            )
            print(fail_box)
            logger.error(f"❌ [Email Failed] Missing SMTP credentials: {', '.join(missing_items)} for {to_email}")
            return {
                "status": "failed",
                "provider": "tenant_smtp" if has_custom_smtp else "smtp",
                "error": f"Missing SMTP credentials ({', '.join(missing_items)})",
                "channel": "email",
                "destination": to_email
            }

        def _send_smtp_sync():
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{sender_name} <{from_email}>"
            msg["To"] = to_email
            if reply_to:
                msg["Reply-To"] = f"{sender_name} <{reply_to}>"

            part1 = MIMEText(body_text, "plain")
            msg.attach(part1)
            if body_html:
                part2 = MIMEText(body_html, "html")
                msg.attach(part2)

            clean_pwd = (smtp_pass or "").strip("'\"").replace(" ", "").strip()
            
            if smtp_port == 465:
                with smtplib.SMTP_SSL(smtp_host, 465, timeout=15) as server:
                    server.login(smtp_user, clean_pwd)
                    server.sendmail(from_email, [to_email], msg.as_string())
            else:
                with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
                    server.ehlo()
                    server.starttls()
                    server.ehlo()
                    server.login(smtp_user, clean_pwd)
                    server.sendmail(from_email, [to_email], msg.as_string())

        try:
            await asyncio.to_thread(_send_smtp_sync)
            logger.info(f"✅ [SMTP Email] Successfully dispatched to {to_email} (Tenant: {tid}, Server: {smtp_host})")
            print(f"✅ [SMTP Email] Successfully dispatched to {to_email} via {smtp_host}:{smtp_port}")
            return {"status": "delivered", "provider": "tenant_smtp" if has_custom_smtp else "smtp", "channel": "email", "destination": to_email}
        except Exception as e:
            fail_box = (
                "\n" + "=" * 65 + "\n"
                f"❌ [EMAIL DISPATCH FAILED — SMTP EXCEPTION]\n"
                f"• Tenant      : {tenant_id} ({b_name})\n"
                f"• Destination : {to_email}\n"
                f"• SMTP Server : {smtp_host}:{smtp_port}\n"
                f"• SMTP User   : {smtp_user}\n"
                f"• Error Type  : {type(e).__name__}\n"
                f"• Error Detail: {str(e)}\n"
                + "=" * 65 + "\n"
            )
            print(fail_box)
            logger.error(f"❌ [SMTP Exception] Failed to deliver email via SMTP ({smtp_host}): {e}\n{traceback.format_exc()}")
            return {
                "status": "failed",
                "provider": "tenant_smtp" if has_custom_smtp else "smtp",
                "error": f"{type(e).__name__}: {str(e)}",
                "channel": "email",
                "destination": to_email
            }

    # 3. Mock Email Simulator (ONLY when EMAIL_PROVIDER is explicitly set to 'mock')
    elif provider == "mock":
        print("\n" + "=" * 65)
        print(f"⚠️ [EMAIL DISPATCH SIMULATOR — MOCK MODE ACTIVE (No real email sent)]")
        print(f"• Tenant      : {tenant_id} ({b_name})")
        print(f"• From        : {sender_name} <{tenant_email or 'support@qwickcall.com'}>")
        print(f"• Reply-To    : {reply_to or 'None'}")
        print(f"• To          : {to_email}")
        print(f"• Subject     : {subject}")
        print(f"• Attachment  : {attachment_url or 'None'}")
        print(f"• Body:\n{body_text}")
        print("=" * 65 + "\n")
        logger.info(f"🟢 [Email Mock] Simulated delivery to {to_email} for tenant {tenant_id}")
        return {
            "status": "simulated",
            "provider": "mock",
            "channel": "email",
            "destination": to_email,
            "note": "Delivered via Mock Email Dispatcher"
        }

    else:
        err_msg = f"Unknown EMAIL_PROVIDER '{provider}'. Supported: smtp, sendgrid, mock."
        logger.error(f"❌ [Email Failed] {err_msg}")
        print(f"\n❌ [EMAIL DISPATCH FAILED] {err_msg}\n")
        return {"status": "failed", "provider": provider, "error": err_msg, "channel": "email", "destination": to_email}


# -----------------------------------------------------------------------------
# High-Level Brochure Dispatch Orchestrator
# -----------------------------------------------------------------------------
def get_tenant_brochure_url(tenant_id: str) -> str:
    """Returns the custom brochure URL or local uploaded PDF for a given tenant."""
    tid = normalize_tenant_id(tenant_id)
    cfg = load_tenant_config(tid) or {}
    rules = cfg.get("business_rules", {})
    if rules.get("brochure_url"):
        return rules["brochure_url"]

    local_brochure = os.path.join(settings.BROCHURES_DIR, f"{tid}_brochure.pdf")
    if os.path.exists(local_brochure):
        return f"/api/v1/tenant/brochure/{tid}"

    return getattr(settings, "DEFAULT_BROCHURE_URL", "https://idigitalstudies.com/assets/brochure.pdf")


async def send_brochure(
    tenant_id: str,
    channel: str,
    destination: str,
    customer_name: Optional[str] = None,
    notes: Optional[str] = None
) -> Dict[str, Any]:
    """
    Dispatches tenant brochure to the caller via WhatsApp or Email.
    - Loads tenant-specific brochure URL and metadata from tenant_config
    - Checks for uploaded brochure in data/brochures/{tenant_id}_brochure.pdf
    - Personalizes message copy dynamically for the tenant's business
    - Calls corresponding channel transport with tenant-specific branding
    """
    tid = normalize_tenant_id(tenant_id)
    cfg = load_tenant_config(tid) or {}
    rules = cfg.get("business_rules", {})

    b_name = cfg.get("business_name") or ("iDigitalStudies" if "digital" in tid else "Customer Support")
    advisor = rules.get("agent_name") or rules.get("advisor_name") or ("Aanya" if "digital" in tid else "Advisor")
    
    # 1. Resolve tenant-specific brochure URL
    brochure_url = get_tenant_brochure_url(tid)

    brochure_title = rules.get("brochure_title") or f"{b_name} Official Information & Brochure"
    
    norm_channel = channel.lower().strip()
    c_name = customer_name or "Valued Customer"

    # Is this tenant an educational institute (like iDigitalStudies)?
    is_edu = "digital" in tid or "study" in tid or "school" in tid or "college" in tid or "institute" in b_name.lower()

    if "what" in norm_channel or norm_channel == "wa":
        # Build WhatsApp Message
        if is_edu:
            wa_message = (
                f"Hello {c_name}! 👋\n\n"
                f"Thank you for contacting *{b_name}*. As requested during your call with {advisor}, "
                f"here is our official *{brochure_title}*:\n\n"
                f"📄 *Download Brochure:* {brochure_url}\n\n"
                f"Key Highlights:\n"
                f"• Practical Industry Training & Live Projects\n"
                f"• Dedicated Placement & Career Support\n"
                f"• Flexible Schedules & Mentorship\n\n"
                f"Feel free to reply directly to this chat if you have any questions! 🚀"
            )
        else:
            wa_message = (
                f"Hello {c_name}! 👋\n\n"
                f"Thank you for contacting *{b_name}*. As requested during your conversation with {advisor}, "
                f"here is our official *{brochure_title}*:\n\n"
                f"📄 *Download Document:* {brochure_url}\n\n"
                f"Please let us know if you have any questions or require further assistance! 🌟"
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
        subject = rules.get("email_subject") or f"Your Requested Information & Brochure — {b_name}"
        
        body_text = (
            f"Dear {c_name},\n\n"
            f"Thank you for your interest in {b_name}.\n\n"
            f"As requested during your call with our advisor {advisor}, please find the details and "
            f"download link for our official brochure below:\n\n"
            f"Brochure Link: {brochure_url}\n\n"
            f"If you have any questions, simply reply directly to this email or call our desk.\n\n"
            f"Warm regards,\n"
            f"{advisor}\n"
            f"{b_name}"
        )

        body_html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 24px; border: 1px solid #e2e8f0; border-radius: 10px; background-color: #ffffff;">
                <h2 style="color: #1e40af; margin-top: 0;">{b_name}</h2>
                <p>Dear <strong>{c_name}</strong>,</p>
                <p>Thank you for speaking with our advisor <strong>{advisor}</strong>. As requested, here is your official information and brochure document.</p>
                <div style="text-align: center; margin: 32px 0;">
                    <a href="{brochure_url}" style="background-color: #2563eb; color: #ffffff; padding: 14px 28px; text-decoration: none; border-radius: 8px; font-weight: bold; display: inline-block; font-size: 15px;">
                        📥 Download Official Brochure (PDF)
                    </a>
                </div>
                <p style="font-size: 14px; color: #64748b;">If you have questions, need clarification, or wish to schedule a consultation, feel free to reply directly to this email.</p>
                <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 24px 0;" />
                <p style="font-size: 13px; color: #64748b; margin-bottom: 0;">
                    Warm regards,<br />
                    <strong>{advisor}</strong><br />
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

