"""
Legacy backward-compatibility shim for app.core.voice_agent.
Canonical implementation is in app.services.voice_agent.agent.
"""
from app.services.voice_agent.agent import *
from app.services.voice_agent.agent import (
    _get_business_info,
    get_tenant_greeting,
    generate_executive_summary,
    _resolve_tenant_from_did,
    _resolve_did_route,
    _log_call_end,
)
