"""Audio & Telephony Protocol Utilities"""
from app.core.audio.audio_utils import (
    pcm_rms,
    resample_pcm_16k_to_8k,
    get_beep_audio_8k,
    generate_beep_pcm,
    apply_office_acoustics,
)
from app.core.audio.sip_client import AsteriskSipClient
from app.core.audio.sip_parser import (
    SipMessage,
    parse_auth_challenge,
    compute_digest_response,
    build_auth_header,
    create_register_request,
    create_options_200_ok,
    create_bye_request,
    create_invite_request,
    create_ack_request,
)

__all__ = [
    "pcm_rms",
    "resample_pcm_16k_to_8k",
    "get_beep_audio_8k",
    "generate_beep_pcm",
    "apply_office_acoustics",
    "AsteriskSipClient",
    "SipMessage",
    "parse_auth_challenge",
    "compute_digest_response",
    "build_auth_header",
    "create_register_request",
    "create_options_200_ok",
    "create_bye_request",
    "create_invite_request",
    "create_ack_request",
]
