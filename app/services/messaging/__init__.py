"""Messaging Service — Multi-Channel WhatsApp & SMTP Email Brochure Dispatch"""
from app.services.messaging.brochure_service import (
    extract_phone_number,
    extract_email_address,
    format_phone_for_speech,
    format_email_for_speech,
    send_brochure,
    send_whatsapp_message,
    send_email_message,
    get_tenant_brochure_url,
)

__all__ = [
    "extract_phone_number",
    "extract_email_address",
    "format_phone_for_speech",
    "format_email_for_speech",
    "send_brochure",
    "send_whatsapp_message",
    "send_email_message",
    "get_tenant_brochure_url",
]

