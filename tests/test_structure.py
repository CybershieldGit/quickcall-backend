"""
Architecture & Import Integrity Test
Validates that both the new modular services/api structure and backward-compat shims load cleanly.
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

def test_modular_service_imports():
    from app.services.voice_agent import VoiceAgent, detect_brochure_intent
    from app.services.ai import call_llm, transcribe_pcm, synthesize_pcm
    from app.services.knowledge import get_tenant_knowledge_text, search_semantic_cache
    from app.services.messaging import send_brochure, extract_email_address
    from app.services.tenant import load_tenant_config
    from app.services.telemetry import log_call_start, CallMonitor
    from app.core.audio import pcm_rms, AsteriskSipClient

    assert callable(VoiceAgent)
    assert callable(call_llm)
    assert callable(transcribe_pcm)
    assert callable(synthesize_pcm)
    assert callable(get_tenant_knowledge_text)
    assert callable(send_brochure)
    assert callable(load_tenant_config)
    assert callable(log_call_start)
    assert callable(pcm_rms)

def test_api_v1_router_imports():
    from app.api import api_router
    assert api_router is not None
    assert len(api_router.routes) > 0

def test_backward_compat_core_imports():
    import app.core as core
    assert hasattr(core, "VoiceAgent")
    assert hasattr(core, "intent_detector")
    assert hasattr(core, "llm_service")
    assert hasattr(core, "rag_engine")
    assert hasattr(core, "messaging_service")
    assert hasattr(core, "tenant_service")

if __name__ == "__main__":
    test_modular_service_imports()
    test_api_v1_router_imports()
    test_backward_compat_core_imports()
    print("✅ All structure and modular import tests passed!")
