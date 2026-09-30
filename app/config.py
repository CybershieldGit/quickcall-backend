import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    PROJECT_NAME: str = "Enterprise Voice Agent Platform (iDigitalStudies)"
    PORT: int = int(os.getenv("PORT", 8001))
    HOST: str = os.getenv("HOST", "0.0.0.0")
    
    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", 
        "postgresql+asyncpg://bufferworks:Q6y1w5t2e4r3@147.79.71.199:5432/ivrcallingagent"
    )
    SYNC_DATABASE_URL: str = os.getenv(
        "SYNC_DATABASE_URL", 
        "postgresql://bufferworks:Q6y1w5t2e4r3@147.79.71.199:5432/ivrcallingagent"
    )

    # API Keys
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    DEEPGRAM_API_KEY: str = os.getenv("DEEPGRAM_API_KEY", "")
    ELEVENLABS_API_KEY: str = os.getenv("ELEVENLABS_API_KEY", "")
    ELEVENLABS_VOICE_ID: str = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
    
    # Twilio
    TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_PHONE_NUMBER: str = os.getenv("TWILIO_PHONE_NUMBER", "")
    DEFAULT_ESCALATION_NUMBER: str = os.getenv("DEFAULT_ESCALATION_NUMBER", "+919876543210").replace(" ", "").replace("-", "").strip()
    
    # Base public URL for webhooks
    BASE_URL: str = os.getenv("BASE_URL", "http://localhost:8001")

settings = Settings()
