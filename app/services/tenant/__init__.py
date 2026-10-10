"""Tenant Service — Multi-Tenant Dynamic Configurations & Phone Number Routing"""
from app.services.tenant.tenant_service import (
    load_tenant_config,
    load_agent_config,
    normalize_tenant_id,
    resolve_tenant_from_did,
    resolve_did_route,
    get_tenant_escalation_target,
    get_current_escalation_number,
    get_tenant_exotel_credentials,
    set_did_tenant_mapping,
    clear_did_cache,
    clear_agent_cache,
)

__all__ = [
    "load_tenant_config",
    "load_agent_config",
    "normalize_tenant_id",
    "resolve_tenant_from_did",
    "resolve_did_route",
    "get_tenant_escalation_target",
    "get_current_escalation_number",
    "get_tenant_exotel_credentials",
    "set_did_tenant_mapping",
    "clear_did_cache",
    "clear_agent_cache",
]
