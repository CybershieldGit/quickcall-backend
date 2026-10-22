import os
import hmac
import hashlib
import secrets
import logging
from dotenv import load_dotenv

# Real environment variables take precedence over .env (standard for containers / CI)
load_dotenv(override=False)

_config_logger = logging.getLogger("qwickcall.config")
_IS_PROD_ENV = os.getenv("ENVIRONMENT", "development").lower() in ("production", "prod")


def _required_secret(name: str) -> str:
    """Returns a secret from env. Refuses to start in production without it; uses an ephemeral value in dev."""
    value = os.getenv(name, "").strip()
    placeholder = any(p in value.lower() for p in ("generate_a_secure", "change_in_prod", "your_", "changeme"))
    if value and not placeholder and len(value) >= 32:
        return value
    if value:
        _config_logger.warning(f"{name} looks like a placeholder or is shorter than 32 characters; ignoring it.")
    if _IS_PROD_ENV:
        raise RuntimeError(f"{name} must be set in the environment when ENVIRONMENT=production.")
    _config_logger.warning(f"{name} is not set; using a random per-process value (development only).")
    return secrets.token_urlsafe(48)

class Settings:
    PROJECT_NAME: str = "Enterprise Voice Agent Platform (iDigitalStudies)"
    PORT: int = int(os.getenv("PORT", 8001))
    HOST: str = os.getenv("HOST", "0.0.0.0")
    
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///qwickcall.db")
    SYNC_DATABASE_URL: str = os.getenv("SYNC_DATABASE_URL", "sqlite:///qwickcall.db")

    # Security & Authentication
    JWT_SECRET_KEY: str = _required_secret("JWT_SECRET_KEY")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))  # 24 hours
    ALLOWED_ORIGINS: str = os.getenv("ALLOWED_ORIGINS", "*")

    # Key for encrypting tenant-supplied secrets (Exotel token, SMTP password) at rest.
    # Defaults to a key derived from JWT_SECRET_KEY; set explicitly so JWT rotation doesn't orphan stored secrets.
    SECRETS_ENCRYPTION_KEY: str = os.getenv("SECRETS_ENCRYPTION_KEY", "")

    # Shared secret appended as ?token= to Exotel applet / webhook URLs. When set, unauthenticated
    # Exotel webhooks and media-stream WebSockets are rejected.
    EXOTEL_WEBHOOK_TOKEN: str = os.getenv("EXOTEL_WEBHOOK_TOKEN", "")

    # Meta app secret used to verify X-Hub-Signature-256 on WhatsApp webhooks
    WHATSAPP_APP_SECRET: str = os.getenv("WHATSAPP_APP_SECRET", "")

    # Hard cap on a single call's length (toll-fraud / runaway-cost guard)
    MAX_CALL_DURATION_SECONDS: int = int(os.getenv("MAX_CALL_DURATION_SECONDS", "900"))

    # Upload limits
    MAX_UPLOAD_BYTES: int = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
    # Uploaded tenant brochures (<project>/data/brochures; /app/data is the writable volume in Docker)
    BROCHURES_DIR: str = os.getenv(
        "BROCHURES_DIR",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "brochures")
    )

    # Verify the Asterisk WSS certificate (disable only for a self-signed lab PBX)
    RPDIGITAL_SIP_TLS_VERIFY: bool = os.getenv("RPDIGITAL_SIP_TLS_VERIFY", "true").lower() in ("true", "1", "yes")

    # API Keys
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    DEEPGRAM_API_KEY: str = os.getenv("DEEPGRAM_API_KEY", "")
    ELEVENLABS_API_KEY: str = os.getenv("ELEVENLABS_API_KEY", "")
    ELEVENLABS_VOICE_ID: str = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
    
    # Exotel Configuration
    EXOTEL_ACCOUNT_SID: str = os.getenv("EXOTEL_ACCOUNT_SID", "")
    EXOTEL_API_KEY: str = os.getenv("EXOTEL_API_KEY", "")
    EXOTEL_API_TOKEN: str = os.getenv("EXOTEL_API_TOKEN", "")
    EXOTEL_SUBDOMAIN: str = os.getenv("EXOTEL_SUBDOMAIN", "api.exotel.com")
    EXOTEL_CALLER_ID: str = os.getenv("EXOTEL_CALLER_ID", "")
    EXOTEL_APP_ID: str = os.getenv("EXOTEL_APP_ID", "")
    raw_esc = os.getenv("DEFAULT_ESCALATION_NUMBER", "").replace(" ", "").replace("-", "").strip()
    # Platform-wide human fallback for transfers when a tenant/agent has none. Empty disables the fallback.
    DEFAULT_ESCALATION_NUMBER: str = raw_esc if len(raw_esc) >= 10 else ""

    # Tenant that serves traffic which can't be attributed to one (unknown DID, legacy widget without
    # tenant_id). Empty means such traffic is rejected instead of being billed to an arbitrary tenant.
    DEFAULT_TENANT_ID: str = os.getenv("DEFAULT_TENANT_ID", "").strip().lower()

    # RP Digital / Asterisk WebRTC SIP Configuration
    RPDIGITAL_ASTERISK_HOST: str = os.getenv("RPDIGITAL_ASTERISK_HOST", "kenyavoice.rpdigitalphone.com")
    RPDIGITAL_ASTERISK_PORT: int = int(os.getenv("RPDIGITAL_ASTERISK_PORT", "5000"))
    RPDIGITAL_ASTERISK_PATH: str = os.getenv("RPDIGITAL_ASTERISK_PATH", "/ws")
    RPDIGITAL_SIP_USER: str = os.getenv("RPDIGITAL_SIP_USER", "")
    RPDIGITAL_SIP_PASSWORD: str = os.getenv("RPDIGITAL_SIP_PASSWORD", "")
    RPDIGITAL_DID_NUMBER: str = os.getenv("RPDIGITAL_DID_NUMBER", "")

    # Environment Mode ('development', 'testing', 'production')
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development").lower()
    IS_PRODUCTION: bool = os.getenv("ENVIRONMENT", "development").lower() in ("production", "prod")

    # STT & TTS Configuration
    # Deepgram is default for development & testing; Sarvam AI is default for production
    _default_tts: str = "sarvam" if os.getenv("ENVIRONMENT", "development").lower() in ("production", "prod") else "deepgram"
    STT_ENGINE: str = os.getenv("STT_ENGINE", "sarvam")  # sarvam, deepgram
    TTS_ENGINE: str = os.getenv("TTS_ENGINE", _default_tts).lower()  # deepgram (dev/test), sarvam (production), edge-tts, elevenlabs
    EDGE_TTS_VOICE_EN: str = os.getenv("EDGE_TTS_VOICE_EN", "en-IN-NeerjaNeural")
    EDGE_TTS_VOICE_HI: str = os.getenv("EDGE_TTS_VOICE_HI", "hi-IN-SwaraNeural")
    DEEPGRAM_VOICE_MODEL: str = os.getenv("DEEPGRAM_VOICE_MODEL", "aura-asteria-en")
    
    # Sarvam AI (Indian English / Hinglish Counselor Voice)
    SARVAM_API_KEY: str = os.getenv("SARVAM_API_KEY", "")
    SARVAM_SPEAKER: str = os.getenv("SARVAM_SPEAKER", "ritu")
    SARVAM_MODEL: str = os.getenv("SARVAM_MODEL", "bulbul:v3")
    SARVAM_LANGUAGE_CODE: str = os.getenv("SARVAM_LANGUAGE_CODE", "en-IN")

    # Office Room Acoustic Presence (Simulates live admissions office desk presence & subtle room reflection)
    ENABLE_OFFICE_ACOUSTICS: bool = os.getenv("ENABLE_OFFICE_ACOUSTICS", "false").lower() in ("true", "1", "yes")
    OFFICE_ACOUSTICS_WET: float = float(os.getenv("OFFICE_ACOUSTICS_WET", "0.0"))

    # Headset Microphone & Admissions Office Floor Ambience (Background counselor chatter + typing)
    ENABLE_HEADSET_OFFICE_SOUND: bool = os.getenv("ENABLE_HEADSET_OFFICE_SOUND", "false").lower() in ("true", "1", "yes")
    OFFICE_AMBIENCE_LEVEL: float = float(os.getenv("OFFICE_AMBIENCE_LEVEL", "0.0"))

    # Redis Cloud Configuration (Semantic Cache)
    REDIS_URL: str = os.getenv("REDIS_URL", "")

    # Base public URL for webhooks / WebSocket (auto-detects active cloudflared quicktunnel if running)
    @property
    def BASE_URL(self) -> str:
        env_base = os.getenv("BASE_URL", "").strip().rstrip("/")
        if env_base and not ("localhost" in env_base or "127.0.0.1" in env_base):
            return env_base
        try:
            import urllib.request, json
            with urllib.request.urlopen("http://127.0.0.1:20241/quicktunnel", timeout=0.1) as resp:
                data = json.loads(resp.read().decode())
                hostname = data.get("hostname")
                if hostname:
                    return f"https://{hostname}"
        except Exception:
            pass
        return env_base or "http://localhost:8001"

    # Wholesale & Margin Economics (INR per min)
    DEFAULT_WHOLESALE_RATE_PER_MIN: float = float(os.getenv("DEFAULT_WHOLESALE_RATE_PER_MIN", "3.00"))
    DEFAULT_DIRECT_RETAIL_RATE_PER_MIN: float = float(os.getenv("DEFAULT_DIRECT_RETAIL_RATE_PER_MIN", "5.00"))
    PLATFORM_BASE_COST_PER_MIN: float = float(os.getenv("PLATFORM_BASE_COST_PER_MIN", "2.00"))

    # Platform Owner / Admin Credentials
    # ADMIN_PASSWORD has no default: when unset, the env-based owner login is disabled
    ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "admin@qwickcall.ai")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "")

    # Messaging & Brochure Dispatch (WhatsApp & Email)
    WHATSAPP_PROVIDER: str = os.getenv("WHATSAPP_PROVIDER", "mock")  # mock, meta, twilio
    WHATSAPP_META_TOKEN: str = os.getenv("WHATSAPP_META_TOKEN", "")
    WHATSAPP_PHONE_NUMBER_ID: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    WHATSAPP_VERIFY_TOKEN: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
    TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_WHATSAPP_FROM: str = os.getenv("TWILIO_WHATSAPP_FROM", "+14155238886")

    EMAIL_PROVIDER: str = os.getenv("EMAIL_PROVIDER", "mock")  # mock, smtp, sendgrid
    SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM_EMAIL: str = os.getenv("SMTP_FROM_EMAIL", "")
    SENDGRID_API_KEY: str = os.getenv("SENDGRID_API_KEY", "")
    SENDGRID_FROM_EMAIL: str = os.getenv("SENDGRID_FROM_EMAIL", "")

    # Default Brochure Details
    DEFAULT_BROCHURE_URL: str = os.getenv("DEFAULT_BROCHURE_URL", "")
    DEFAULT_BROCHURE_TITLE: str = os.getenv("DEFAULT_BROCHURE_TITLE", "Course Curriculum & Information Brochure")

    # Conversational Fillers Toggle (Disabled by default to avoid unnatural/canned prefixes)
    ENABLE_CONVERSATIONAL_FILLERS: bool = os.getenv("ENABLE_CONVERSATIONAL_FILLERS", "false").lower() in ("true", "1", "yes")

    @property
    def INTERNAL_BRIDGE_TOKEN(self) -> str:
        """Token the in-process SIP client presents to the /sip/stream loopback WebSocket (stable across workers)."""
        return hmac.new(self.JWT_SECRET_KEY.encode(), b"qwickcall-sip-bridge", hashlib.sha256).hexdigest()


settings = Settings()
