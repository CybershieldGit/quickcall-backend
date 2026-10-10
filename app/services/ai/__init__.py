"""AI Service — Multi-tier LLMs, Real-Time STT and Indian Multilingual TTS Engines"""
from app.services.ai.llm_service import call_llm, call_llm_stream
from app.services.ai.stt_engine import transcribe_pcm
from app.services.ai.tts_engine import (
    synthesize_pcm,
    stream_synthesize_pcm,
    generate_speech,
    clean_text_for_speech,
    get_inaudible_prompt_message,
    get_graceful_transfer_message,
)
from app.services.ai.ram_fallback import get_ram_fallback_response, ram_fallback_stream

__all__ = [
    "call_llm",
    "call_llm_stream",
    "transcribe_pcm",
    "synthesize_pcm",
    "stream_synthesize_pcm",
    "generate_speech",
    "clean_text_for_speech",
    "get_inaudible_prompt_message",
    "get_graceful_transfer_message",
    "get_ram_fallback_response",
    "ram_fallback_stream",
]
