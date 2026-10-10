"""
Audio Processing Utilities
Provides Python 3.11/3.12/3.13 cross-version compatible RMS and audio formatting routines.
Safely replaces audioop if running in Python 3.13 where audioop was removed.
"""
import math
import struct
from typing import Tuple, Optional

try:
    import audioop
except ImportError:
    audioop = None


def pcm_rms(raw_bytes: bytes, width: int = 2) -> int:
    """Calculates RMS (Root Mean Square) energy of 16-bit Linear PCM audio."""
    if not raw_bytes:
        return 0

    if audioop is not None:
        try:
            return audioop.rms(raw_bytes, width)
        except Exception:
            pass

    # High-performance cross-version fallback for Python 3.13+
    count = len(raw_bytes) // 2
    if count == 0:
        return 0
    try:
        samples = struct.unpack(f"<{count}h", raw_bytes[:count * 2])
        sum_sq = sum(s * s for s in samples)
        return int(math.sqrt(sum_sq / count))
    except Exception:
        return 0


def resample_pcm_16k_to_8k(pcm_16k: bytes) -> bytes:
    """Downsamples 16000Hz 16-bit mono PCM to 8000Hz by decimation."""
    if not pcm_16k:
        return b""
    if audioop is not None:
        try:
            res, _ = audioop.ratecv(pcm_16k, 2, 1, 16000, 8000, None)
            return res
        except Exception:
            pass

    # Simple 2:1 sample decimation for 16kHz -> 8kHz
    count = len(pcm_16k) // 2
    if count < 2:
        return pcm_16k
    samples = struct.unpack(f"<{count}h", pcm_16k[:count * 2])
    # Pick every second sample
    decimated = samples[::2]
    return struct.pack(f"<{len(decimated)}h", *decimated)


# =========================================================================
# Prompt Beep Tone Audio Generator (For Email / Dictation Turn Cues)
# =========================================================================
_CACHED_BEEP_8K: Optional[bytes] = None

def generate_beep_pcm(freq_hz: int = 950, duration_ms: int = 240, sample_rate: int = 8000, volume: float = 0.5) -> bytes:
    """
    Generates a clear, gentle 16-bit Linear PCM sine wave tone with smooth attack and decay
    to signal callers when to start speaking their email or phone number.
    """
    total_samples = int(sample_rate * (duration_ms / 1000.0))
    samples = []
    fade_len = int(sample_rate * 0.015)  # 15ms smooth ramp to prevent speaker click
    for i in range(total_samples):
        val = math.sin(2.0 * math.pi * freq_hz * (i / sample_rate))
        if i < fade_len:
            val *= (i / fade_len)
        elif i > total_samples - fade_len:
            val *= ((total_samples - i) / fade_len)
        s16 = max(-32768, min(32767, int(val * volume * 32767)))
        samples.append(s16)
    return struct.pack(f"<{len(samples)}h", *samples)


def get_beep_audio_8k() -> bytes:
    """Returns cached 8kHz Linear PCM audio of the dictation cue beep tone."""
    global _CACHED_BEEP_8K
    if _CACHED_BEEP_8K is None:
        _CACHED_BEEP_8K = generate_beep_pcm(freq_hz=950, duration_ms=240, sample_rate=8000, volume=0.55)
    return _CACHED_BEEP_8K


# =========================================================================
# Office Room Acoustic Presence & Early Reflection Modeling
# =========================================================================

try:
    import numpy as np
except ImportError:
    np = None


_AMBIENCE_CACHE = {}


def get_office_ambience_loop(sample_rate: int = 8000, duration_sec: float = 45.0):
    """
    Loads or generates a seamless, looping audio buffer representing an active admissions counseling office.
    Layers:
    1. Distant admissions counselors on calls with other students — muffled, hazy, unintelligible murmur (filtered <580Hz).
    2. Subtle HVAC / office room tone.
    3. Distant soft keyboard typing.
    """
    key = (sample_rate, int(duration_sec))
    if key in _AMBIENCE_CACHE:
        return _AMBIENCE_CACHE[key]

    if np is None:
        return None

    import os
    raw_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "admissions_office_ambience_8k.raw")
    if os.path.exists(raw_path) and sample_rate == 8000:
        try:
            with open(raw_path, "rb") as f:
                raw_bytes = f.read()
            samples = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            _AMBIENCE_CACHE[key] = samples
            return samples
        except Exception as e:
            print(f"[AmbienceLoader] Error loading {raw_path}: {e}")

    n_samples = int(duration_sec * sample_rate)
    t = np.linspace(0, duration_sec, n_samples, endpoint=False)

    # 1. Base floor tone: pink room tone filtered between 100Hz and 2200Hz
    np.random.seed(101)
    white = np.random.randn(n_samples)
    fft = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n_samples, 1.0 / sample_rate)
    mask = (freqs >= 100) & (freqs <= 2400)
    fft[~mask] = 0
    fft[mask] /= np.sqrt(np.maximum(freqs[mask], 50))
    room_tone = np.fft.irfft(fft, n=n_samples)
    room_tone = room_tone / (np.max(np.abs(room_tone)) + 1e-6)

    # 2. Distant admissions counselors on calls (2-3 background counselors)
    counselor_murmur = np.zeros(n_samples)
    for voice_id in range(3):
        base_pitch = np.random.uniform(140, 230)
        speech_cadence = np.random.uniform(3.0, 4.5)
        syllables = np.maximum(0, np.sin(2 * np.pi * speech_cadence * t + np.random.uniform(0, 2 * np.pi))) ** 2
        phrase_cadence = np.random.uniform(0.18, 0.28)
        sentence_envelope = np.maximum(0, np.sin(2 * np.pi * phrase_cadence * t + np.random.uniform(0, 2 * np.pi))) ** 3
        vocal_sound = np.zeros(n_samples)
        for h in range(1, 9):
            freq = base_pitch * h
            if freq > 3400:
                continue
            gain = 1.0 / (h ** 1.2)
            if 400 <= freq <= 900 or 1100 <= freq <= 2400:
                gain *= 2.2
            vocal_sound += gain * np.sin(2 * np.pi * freq * t + np.random.uniform(0, 2 * np.pi))
        counselor_murmur += vocal_sound * syllables * sentence_envelope * 0.12

    # 3. CRM keyboard typing
    typing = np.zeros(n_samples)
    click_times = np.random.exponential(scale=0.6, size=int(duration_sec * 3.0))
    click_indices = np.cumsum(click_times * sample_rate).astype(int)
    click_indices = click_indices[click_indices < n_samples - 150]
    click_shape = np.sin(2 * np.pi * 1600 * np.linspace(0, 0.012, 100)) * np.exp(-np.linspace(0, 12, 100))
    for idx in click_indices:
        if np.random.rand() > 0.4:
            typing[idx : idx + len(click_shape)] += click_shape * np.random.uniform(0.08, 0.22)

    # 4. Blend and create seamless loop
    ambience = room_tone * 0.25 + counselor_murmur * 0.55 + typing * 0.20
    fade_len = int(0.5 * sample_rate)
    fade_in = np.linspace(0, 1, fade_len)
    fade_out = np.linspace(1, 0, fade_len)
    ambience[:fade_len] = ambience[:fade_len] * fade_in + ambience[-fade_len:] * fade_out
    ambience = (ambience / (np.max(np.abs(ambience)) + 1e-6)).astype(np.float32)
    _AMBIENCE_CACHE[key] = ambience
    return ambience


class OfficeAcousticsFilter:
    """
    Real-time stateful DSP filter simulating an Admissions Counseling Office room presence.
    Features:
    1. Headset Boom Mic Equalization:
       - Rolls off unnatural sub-bass rumble (<100Hz) and enhances speech presence (2.5kHz).
    2. Workstation Early Reflections:
       - Simulates desk bounce (~20ms) and office wall/partition boundary (~46ms).
    3. Active Counseling Floor Ambience:
       - Seamlessly blends faint distant background murmur of other counselors on calls
         and subtle CRM keyboard typing into the open headset microphone.
    """
    def __init__(
        self,
        sample_rate: int = 8000,
        wet_mix: float = 0.0,
        ambience_level: float = 0.0,
        headset_eq: bool = False
    ):
        self.sample_rate = sample_rate
        self.wet_mix = max(0.0, min(0.40, wet_mix))
        self.ambience_level = max(0.0, min(0.50, ambience_level))
        self.headset_eq = headset_eq

        # Reflection 1: Desk / workstation reflection (~20ms)
        self.d1 = max(1, int(0.020 * sample_rate))
        # Reflection 2: Office wall / door boundary (~46ms)
        self.d2 = max(1, int(0.046 * sample_rate))
        self.max_delay = max(self.d1, self.d2)

        if np is not None:
            self._history = np.zeros(self.max_delay, dtype=np.float32)
            self._ambience_loop = get_office_ambience_loop(sample_rate=sample_rate)
            self._amb_pos = np.random.randint(0, len(self._ambience_loop) // 2) if self._ambience_loop is not None and len(self._ambience_loop) > 0 else 0
        else:
            self._py_history = [0.0] * self.max_delay
            self._ambience_loop = None
            self._amb_pos = 0

        self._last_ref = 0.0
        self._prev_x = 0.0

    def process_chunk(self, pcm_bytes: bytes) -> bytes:
        if not pcm_bytes:
            return pcm_bytes
        if self.ambience_level <= 0.0 and self.wet_mix <= 0.0 and not self.headset_eq:
            return pcm_bytes

        if np is not None:
            in_samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
            n = len(in_samples)
            if n == 0:
                return pcm_bytes

            # Direct speech: 100% natural, warm, and crystal-clear (no metallic EQ tint)
            speech = in_samples

            # 2. Desk & Wall Early Reflections
            if self.wet_mix > 0.0:
                combined = np.concatenate([self._history, speech])
                start = self.max_delay
                ref1 = combined[start - self.d1 : start - self.d1 + n]
                ref2 = combined[start - self.d2 : start - self.d2 + n]

                # High-frequency damping on reflections (wood/carpet absorption)
                ref1_damped = 0.70 * ref1
                ref1_damped[1:] += 0.30 * ref1[:-1]
                ref1_damped[0] += 0.30 * self._last_ref
                self._last_ref = float(ref1[-1])

                wet_signal = (0.70 * ref1_damped + 0.30 * ref2) * self.wet_mix
                self._history = combined[-self.max_delay :]
            else:
                wet_signal = 0.0

            # 3. Background Counseling Office Ambience (other counselors + typing)
            if self.ambience_level > 0.0 and self._ambience_loop is not None and len(self._ambience_loop) > 0:
                end_pos = self._amb_pos + n
                loop_len = len(self._ambience_loop)
                if end_pos <= loop_len:
                    amb_slice = self._ambience_loop[self._amb_pos : end_pos]
                    self._amb_pos = end_pos % loop_len
                else:
                    part1 = self._ambience_loop[self._amb_pos :]
                    part2 = self._ambience_loop[: end_pos - loop_len]
                    amb_slice = np.concatenate([part1, part2])
                    self._amb_pos = len(part2)
                bg_bleed = amb_slice * (self.ambience_level * 32767.0)
            else:
                bg_bleed = 0.0

            # Composite audio output
            out_samples = speech + wet_signal + bg_bleed

            # Soft clip to 16-bit PCM integer range
            return np.clip(out_samples, -32768, 32767).astype(np.int16).tobytes()
        else:
            # Pure Python fallback
            count = len(pcm_bytes) // 2
            if count == 0:
                return pcm_bytes
            samples = list(struct.unpack(f"<{count}h", pcm_bytes[:count * 2]))
            out = []
            for s in samples:
                r1 = self._py_history[-self.d1]
                r2 = self._py_history[-self.d2]
                r1_damped = 0.70 * r1 + 0.30 * self._last_ref
                self._last_ref = r1
                wet = (0.70 * r1_damped + 0.30 * r2) * self.wet_mix
                val = int(s + wet)
                val = max(-32768, min(32767, val))
                out.append(val)
                self._py_history.append(float(s))
                self._py_history.pop(0)
            return struct.pack(f"<{len(out)}h", *out)

    def get_room_presence_chunk(self, chunk_size: int = 640) -> bytes:
        """Returns a standalone chunk of the admissions office ambient bed for natural room breaths."""
        n = chunk_size // 2
        if np is None or self._ambience_loop is None or len(self._ambience_loop) == 0 or self.ambience_level <= 0.0:
            return b"\x00" * chunk_size
        loop_len = len(self._ambience_loop)
        end_pos = self._amb_pos + n
        if end_pos <= loop_len:
            amb_slice = self._ambience_loop[self._amb_pos : end_pos]
            self._amb_pos = end_pos % loop_len
        else:
            part1 = self._ambience_loop[self._amb_pos :]
            part2 = self._ambience_loop[: end_pos - loop_len]
            amb_slice = np.concatenate([part1, part2])
            self._amb_pos = len(part2)
        bg = amb_slice * (self.ambience_level * 32767.0 * 0.90)
        return np.clip(bg, -32768, 32767).astype(np.int16).tobytes()

    def reset(self):
        if np is not None:
            self._history = np.zeros(self.max_delay, dtype=np.float32)
        else:
            self._py_history = [0.0] * self.max_delay
        self._last_ref = 0.0
        self._prev_x = 0.0


def apply_office_acoustics(
    pcm_bytes: bytes,
    sample_rate: int = 8000,
    wet_mix: float = 0.0,
    ambience_level: float = 0.0,
    headset_eq: bool = False
) -> bytes:
    """One-shot helper to apply headset mic EQ, reflections, and office floor ambience."""
    if not pcm_bytes:
        return pcm_bytes
    filter_inst = OfficeAcousticsFilter(
        sample_rate=sample_rate,
        wet_mix=wet_mix,
        ambience_level=ambience_level,
        headset_eq=headset_eq
    )
    return filter_inst.process_chunk(pcm_bytes)
