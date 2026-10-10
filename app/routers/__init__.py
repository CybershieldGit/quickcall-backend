"""
Routers Re-export Layer (Lazy resolver for backward compatibility with app.routers)
"""
import importlib

_ROUTER_MAPPINGS = {
    "exotel_voice": "app.api.v1.telephony.exotel_voice",
    "exotel_stream": "app.api.v1.telephony.exotel_stream",
    "sip_stream": "app.api.v1.telephony.sip_stream",
    "outbound_call": "app.api.v1.telephony.outbound",
    "chat": "app.api.v1.chat.chat_router",
    "tenant_portal": "app.api.v1.portals.tenant_portal",
    "admin_portal": "app.api.v1.portals.admin_portal",
    "reseller_portal": "app.api.v1.portals.reseller_portal",
    "analytics": "app.api.v1.analytics.analytics",
    "simulator_api": "app.api.v1.simulator.simulator",
}

def __getattr__(name: str):
    if name in _ROUTER_MAPPINGS:
        mod = importlib.import_module(_ROUTER_MAPPINGS[name])
        globals()[name] = mod
        return mod
    raise AttributeError(f"module 'app.routers' has no attribute '{name}'")
