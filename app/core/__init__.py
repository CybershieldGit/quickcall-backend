"""
Core and Services Re-export Layer (Lazy dynamic module resolver for backward compatibility)
"""
import importlib

_MODULE_MAPPINGS = {
    "VoiceAgent": ("app.services.voice_agent.agent", "VoiceAgent"),
    "intent_detector": ("app.services.voice_agent.intent_detector", None),
    "fillers": ("app.services.voice_agent.fillers", None),
    "llm_service": ("app.services.ai.llm_service", None),
    "stt_engine": ("app.services.ai.stt_engine", None),
    "tts_engine": ("app.services.ai.tts_engine", None),
    "ram_fallback": ("app.services.ai.ram_fallback", None),
    "rag_engine": ("app.services.knowledge.rag_engine", None),
    "pdf_processor": ("app.services.knowledge.pdf_processor", None),
    "semantic_cache": ("app.services.knowledge.semantic_cache", None),
    "messaging_service": ("app.services.messaging.brochure_service", None),
    "tenant_service": ("app.services.tenant.tenant_service", None),
    "call_logger": ("app.services.telemetry.call_logger", None),
    "call_monitor": ("app.services.telemetry.call_monitor", None),
    "summarizer": ("app.services.telemetry.summarizer", None),
    "audio_utils": ("app.core.audio.audio_utils", None),
    "sip_client": ("app.core.audio.sip_client", None),
    "sip_parser": ("app.core.audio.sip_parser", None),
    "session_store": ("app.core.session_store", None),
}

def __getattr__(name: str):
    if name in _MODULE_MAPPINGS:
        mod_path, attr_name = _MODULE_MAPPINGS[name]
        mod = importlib.import_module(mod_path)
        if attr_name:
            val = getattr(mod, attr_name)
        else:
            val = mod
        globals()[name] = val
        return val
    raise AttributeError(f"module 'app.core' has no attribute '{name}'")
