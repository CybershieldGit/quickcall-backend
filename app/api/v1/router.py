"""
QwickCall Enterprise API Router (v1)
Consolidates Telephony WebSockets, Conversational Chat, Portals, Analytics & Simulator
"""
from fastapi import APIRouter

from app.api.v1.telephony import exotel_voice, exotel_stream, sip_stream, outbound
from app.api.v1.chat import chat_router
from app.api.v1.portals import tenant_portal, admin_portal, reseller_portal
from app.api.v1.analytics import analytics
from app.api.v1.simulator import simulator

api_router = APIRouter()

# 1. Telephony WebSockets & Webhooks
api_router.include_router(exotel_voice.router)
api_router.include_router(exotel_stream.router)
api_router.include_router(sip_stream.router)
api_router.include_router(outbound.router)

# 2. Conversational Chat Agent (Web & WhatsApp)
api_router.include_router(chat_router.router)

# 3. Management & Partner Portals
api_router.include_router(tenant_portal.router)
api_router.include_router(admin_portal.router)
api_router.include_router(reseller_portal.router)

# 4. Telemetry & Analytics
api_router.include_router(analytics.router)

# 5. Testing Simulator Sandbox
api_router.include_router(simulator.router)
