"""
Enterprise Real-Time Voice Call Monitoring & Telemetry Logger
Provides clean, structured, high-visibility terminal logs for every phase:
- 📞 Call Connect & Metadata
- 🎤 STT Transcriptions & Latencies
- 🧠 Semantic Cache Checks (Hit/Miss/Bypass)
- ⚡ Conversational Fillers (Dispatched/Skipped)
- 🤖 LLM Token Streaming & Sentences
- 🎙️ TTS Synthesis Pipelining
- ⏱️ Turn Latency Breakdown & Call Outcomes
"""
import sys
import time
from typing import Optional, List, Dict, Any, Union

# ANSI Terminal Color Palette
CYAN = "\033[1;36m"
GREEN = "\033[1;32m"
YELLOW = "\033[1;33m"
BLUE = "\033[1;34m"
MAGENTA = "\033[1;35m"
RED = "\033[1;31m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


class CallMonitor:
    @staticmethod
    def call_connected(call_id: str, caller: Optional[str], called: Optional[str], tenant_id: str, stream_id: Optional[str] = None):
        border = "═" * 74
        print(f"\n{CYAN}{border}{RESET}")
        print(f"{CYAN}📞 [CALL CONNECTED]{RESET} {BOLD}ID:{RESET} {call_id[:20]} | {BOLD}From:{RESET} {caller or 'Unknown'} | {BOLD}DID:{RESET} {called or 'Default'} | {BOLD}Tenant:{RESET} {tenant_id}")
        print(f"{DIM}   Stream: {stream_id or 'none'}{RESET}")
        print(f"{CYAN}{border}{RESET}")

    @staticmethod
    def greeting(call_id: str, text: str):
        clean = text.strip().replace("\n", " ")
        print(f"{BLUE}🤖 [GREETING]{RESET} \"{clean[:80]}...\"")

    @staticmethod
    def barge_in(call_id: str, rms: int, threshold: Union[int, str]):
        print(f"{RED}🛑 [BARGE-IN]{RESET} Caller interrupted agent playback (RMS={rms} >= {threshold}) -> Audio halted instantly.")

    @staticmethod
    def stt_result(turn_idx: int, text: str, elapsed_ms: float):
        clean = text.strip()
        print(f"\n{BOLD}{'─' * 74}{RESET}")
        print(f"{BOLD}[TURN #{turn_idx}]{RESET}")
        print(f"{GREEN}🎤 [STT]{RESET}          {elapsed_ms:5.1f}ms │ \"{clean}\"")

    @staticmethod
    def cache_check(is_hit: bool, similarity: float, elapsed_ms: float, matched_query: Optional[str] = None, reason: Optional[str] = None):
        if is_hit:
            print(f"{MAGENTA}🧠 [CACHE]{RESET}        {elapsed_ms:5.1f}ms │ {BOLD}🎯 HIT ({similarity:.1%}){RESET} -> \"{matched_query or ''}\" (0 LLM cost)")
        elif reason:
            print(f"{MAGENTA}🧠 [CACHE]{RESET}        {elapsed_ms:5.1f}ms │ {DIM}BYPASS ({reason}){RESET}")
        else:
            sim_str = f"max sim: {similarity:.1%}" if similarity > 0 else "0 matches"
            print(f"{MAGENTA}🧠 [CACHE]{RESET}        {elapsed_ms:5.1f}ms │ {DIM}MISS ({sim_str}){RESET}")

    @staticmethod
    def filler_event(dispatched: bool, filler_word: Optional[str] = None, chunk_count: int = 0):
        if dispatched and filler_word:
            duration_ms = chunk_count * 40
            print(f"{YELLOW}⚡ [FILLER]{RESET}       <1.0ms │ {BOLD}DISPATCHED:{RESET} \"{filler_word}\" ({chunk_count} chunks, ~{duration_ms}ms audio)")
        else:
            print(f"{YELLOW}⚡ [FILLER]{RESET}       <1.0ms │ {DIM}SKIPPED (Direct answer){RESET}")

    @staticmethod
    def llm_sentence(s_idx: int, text: str, elapsed_ms: float):
        clean = text.strip().replace("\n", " ")
        tag = "Sentence #1 (TTFB)" if s_idx == 1 else f"Sentence #{s_idx}"
        print(f"{BLUE}🤖 [LLM]{RESET}          {elapsed_ms:5.1f}ms │ {tag}: \"{clean[:65]}...\"")

    @staticmethod
    def tts_sentence(s_idx: int, ttfb_ms: float, chunk_count: int = 0):
        print(f"{MAGENTA}🎙️ [TTS]{RESET}          {ttfb_ms:5.1f}ms │ Sentence #{s_idx} first audio chunk ready ({chunk_count} chunks)")

    @staticmethod
    def turn_timeline(
        turn_idx: int,
        vad_ms: float,
        stt_ms: float,
        first_audio_ms: float,
        total_audio_chunks: int,
        has_filler: bool,
        filler_name: str = "",
        is_cache_hit: bool = False
    ):
        filler_note = f"Yes ('{filler_name}')" if has_filler else ("Cache Hit" if is_cache_hit else "Direct")
        print(f"{BOLD}⏱️ [TURN TIMELINE]{RESET}")
        print(f"   ├─ 1. VAD Silence Detection : {vad_ms:5.1f} ms")
        print(f"   ├─ 2. STT Transcription     : {stt_ms:5.1f} ms")
        print(f"   ├─ 3. Filler / Fast Start   : {filler_note}")
        print(f"   └─ 4. Total Playback Chunks : {total_audio_chunks} chunks (~{total_audio_chunks * 40}ms)")
        print(f"   {GREEN}🚀 CALLER-TO-SPEECH LATENCY  : {BOLD}{first_audio_ms:5.1f} ms{RESET}")
        print(f"{BOLD}{'─' * 74}{RESET}")

    @staticmethod
    def call_ended(call_id: str, duration_sec: float, outcome: str, transferred: bool, esc_num: str = ""):
        border = "═" * 74
        esc_info = f" | Transferred to: {esc_num}" if transferred and esc_num else ""
        print(f"\n{RED}{border}{RESET}")
        print(f"{RED}📴 [CALL ENDED]{RESET} {BOLD}ID:{RESET} {call_id[:20]} | {BOLD}Duration:{RESET} {duration_sec:.1f}s | {BOLD}Outcome:{RESET} {outcome}{esc_info}")
        print(f"{RED}{border}{RESET}\n")

    @staticmethod
    def error(call_id: str, component: str, message: str, exc: Optional[Exception] = None):
        border = "─" * 74
        err_detail = f" | Exception: {exc}" if exc else ""
        print(f"\n{RED}{border}{RESET}")
        print(f"{RED}❌ [CALL ERROR]{RESET} [{component}] {BOLD}Call:{RESET} {call_id[:20] if call_id else 'N/A'}")
        print(f"{RED}   Message:{RESET} {message}{err_detail}")
        print(f"{RED}{border}{RESET}")

    @staticmethod
    def warning(call_id: str, component: str, message: str):
        print(f"{YELLOW}⚠️  [CALL WARNING]{RESET} [{component}] Call: {call_id[:20] if call_id else 'N/A'} │ {message}")

