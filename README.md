# QwickCall — Enterprise AI Calling Agent & Omnichannel Platform 🎙️

An ultra-low latency, multi-tenant AI voicebot platform engineered for high-concurrency inbound and outbound phone calls on Indian telecom infrastructure. 

QwickCall is built for real-world business conversations (e.g. admissions counseling, customer care, sales qualification, appointment booking) with native support for **bilingual English & Hindi/Hinglish**, **natural barge-in interruption**, **ambient office acoustics**, and **instant omnichannel follow-up via WhatsApp and Email**.

---

## 🏗️ High-Level System Architecture

```mermaid
flowchart TD
    Caller([📞 Caller / Student Phone Line]) 
    
    subgraph Telephony ["1. Telephony Ingestion Layer"]
        RPDigital["RP Digital Asterisk SIP<br/>(Unlimited Free Airtime @ ₹0.00/min)"]
        Exotel["Exotel Cloud Telephony<br/>(Virtual DID / Smart Flow)"]
    end
    
    Caller <==>|"16-bit Linear PCM (8kHz Mono)"| Telephony
    
    subgraph CoreEngine ["2. QwickCall Real-Time Processing Engine"]
        DIDRouter["Tenant DID Resolution<br/>(Resolves DID → Tenant Persona & KB)"]
        VAD["VAD & Barge-In Handler<br/>(Stops playback on caller speech)"]
        
        subgraph STT ["Speech-to-Text (STT)"]
            SarvamSTT["Sarvam AI saaras:v3 (Primary)<br/>Native Indian Multilingual"]
            DeepgramSTT["Deepgram Nova-2 (Fallback)"]
        end
        
        subgraph Brain ["Intelligence & Reasoning (LLM)"]
            Cache["Redis Semantic Cache<br/>(Vector Cache: 0ms, ₹0.00)"]
            GroqLLM["Tier 1: Groq LPU<br/>(Llama 3.3 70B / Qwen 27B)"]
            GeminiLLM["Tier 2: Gemini Flash Lite<br/>(Automated Failover)"]
            RAMLLM["Tier 3: In-Memory RAM Engine<br/>(Zero-Network Fail-safe)"]
            RAG["PostgreSQL + pgvector<br/>(PDF Knowledge Chunks)"]
        end
        
        subgraph TTS ["Text-to-Speech (TTS)"]
            RAMCache["RAM Pre-Cached Greetings<br/>(0ms Latency, 0 API Cost)"]
            SarvamTTS["Sarvam AI bulbul:v3<br/>(WebSocket Streaming)"]
            EdgeTTS["Microsoft Edge-TTS<br/>(Streaming Neural: ₹0.00)"]
            AuraTTS["Deepgram Aura TTS"]
        end
        
        Acoustics["Office Acoustic Engine<br/>(Headset EQ + Subtle Room Ambience)"]
    end
    
    Telephony <--> DIDRouter
    DIDRouter <--> VAD
    VAD --> STT
    STT --> Brain
    Brain <--> RAG
    Brain --> TTS
    TTS --> Acoustics
    Acoustics --> Telephony

    subgraph AddonLayer ["3. Asynchronous Omnichannel Dispatch (Add-On)"]
        IntentCheck{"Caller asks for<br/>Brochure / Syllabus?"}
        MetaWA["WhatsApp Cloud API<br/>(Sends PDF Brochure @ ₹0.14)"]
        GmailAPI["Gmail API / SMTP<br/>(Sends Syllabus & Fees @ ₹0.03)"]
    end
    
    Brain -.->|Non-Blocking Async Event| IntentCheck
    IntentCheck -->|If Tenant Add-On Enabled| MetaWA
    IntentCheck -->|If Tenant Add-On Enabled| GmailAPI
```

---

## ⚡ Key Architectural Capabilities

### 1. Ultra-Low Latency Conversational Turnaround (~750ms – 900ms)
* **True Streaming Pipeline:** STT streams raw 16-bit 8000Hz PCM directly into sentence-boundary streaming LLM generation, which feeds live audio frames into PyAV MP3/PCM decoders without waiting for full turn completion.
* **Pre-Cached Static Greetings:** Standard greetings (*"Namaste! iDigitalStudies mein aapka swagat hai..."*) and emergency line fail-safes are synthesized once at server startup and cached in RAM for **0ms initial response time and 0 API cost**.

### 2. Multi-Tier Self-Healing LLM Fallback (Zero Dead-Air)
Calls never drop or go silent due to third-party API rate limits:
* **Tier 1 (Primary):** **Groq LPU** (`llama-3.3-70b-versatile` / `qwen/qwen3.8-27b`) delivering <350ms token generation.
* **Tier 2 (Secondary Fallback):** **Google Gemini Flash Lite** via direct HTTP REST & Server-Sent Events (SSE).
* **Tier 3 (Zero-Network RAM Fallback):** In-memory regex & intent matcher that answers core questions (fees, timings, syllabus, placements) with 0 network calls and 0ms latency.

### 3. Native Indian Bilingual Support (English & Hindi/Hinglish)
* Automatic lexical and script detection.
* The agent seamlessly speaks colloquial Hinglish (*"Ji bilkul, main aapko batch timings aur placement support ke baare mein batati hoon..."*) or professional English based on caller choice.

### 4. Natural VAD Barge-In Interruption
* Detects when the human speaks while the agent is talking.
* Instantly drops audio playback buffers on the SIP/WebSocket channel so the caller never talks over the agent.

### 5. Office Acoustic Simulation
* Optional acoustic DSP filter ([app/core/audio_utils.py](file:///Users/CST_TECH/Desktop/callingagent/QwickCall/app/core/audio_utils.py)) applying call-center headset EQ, subtle desk early-reflections, and faint background office chatter to eliminate the artificial "sterile robot" voice feel.

### 6. Isolated Multi-Tenant & Reseller Engine
* **Dynamic DID Routing:** Each inbound virtual number automatically binds to its tenant's persona, knowledge base, human escalation phone number, and custom guardrails.
* **Dual-Wallet Margins:** Real-time billing transactions track Client Retail Rate (e.g. ₹5.00/min), Reseller Wholesale Rate (e.g. ₹3.00/min), and Platform Operational Cost.

---

## 💰 Operational Economics & Real-Time Costing

### 1. Standalone Voice Calling Cost (Per Call Minute)
With **RP Digital providing 100% unlimited free inbound & outbound airtime**:

| Component | Provider & Model | Live Commercial Rate | Effective Cost / Minute |
| :--- | :--- | :--- | :--- |
| **Carrier Airtime** | **RP Digital SIP Trunk** | Flat Monthly DID Plan | **₹0.000 / min** |
| **Speech-to-Text (STT)** | **Sarvam AI `saaras:v3`** | ₹30 / hour of audio | **₹0.183 / min** *(~22s caller speech)* |
| **LLM Inference** | **Groq Llama 3.3 70B** | $0.59 / $0.79 per 1M tokens | **₹0.120 / min** *(with Redis Cache)* |
| **Text-to-Speech (TTS)** | **Sarvam AI `bulbul:v3`** | ₹30 per 10,000 characters | **₹1.500 / min** *(~500 chars spoken)* |
| **Static Greetings** | **In-Memory RAM Cache** | Pre-warmed at startup | **₹0.000 / min** |
| **TOTAL VARIABLE OP COST** | | | **₹1.80 / minute** |

> **Average Cost per 60-Second Completed Call:** **₹1.80**  
> *(If using **Microsoft Edge-TTS** (`TTS_ENGINE=edge-tts`), variable cost drops to **₹0.30 / minute**).*

### 2. Omnichannel Action Add-On (WhatsApp & Email Dispatch)
When a caller asks for a brochure or syllabus over the phone, the AI dispatches it asynchronously:

| Action | Provider | Unit Cost | Call Latency Added |
| :--- | :--- | :--- | :--- |
| **WhatsApp Brochure PDF** | Meta Cloud API (Utility) | **₹0.135** *(Meta ₹0.115 + LLM ₹0.02)* | **0ms** *(Async background task)* |
| **Gmail Syllabus Email** | Gmail API / SMTP | **₹0.030** *(LLM formatting only)* | **0ms** *(Async background task)* |

---

## 📂 Project Directory Structure

```
QwickCall/
├── app/
│   ├── config.py                 # Central platform settings & environment variables
│   ├── main.py                   # FastAPI server, lifecycle warmup, routing registration
│   ├── core/
│   │   ├── audio_utils.py        # PCM resampling, RMS energy, Office Acoustics DSP filter
│   │   ├── call_logger.py        # Real-time operational cost auditing & billing settlement
│   │   ├── call_monitor.py       # Live active call tracking & console metrics
│   │   ├── fillers.py            # Natural filler audio generator ("Ji bilkul...", "Sure...")
│   │   ├── intent_detector.py    # Rule-based & lexical intent extraction
│   │   ├── llm_service.py        # 3-Tier LLM streaming (Groq -> Gemini -> RAM engine)
│   │   ├── pdf_processor.py      # PDF parsing and text chunking
│   │   ├── rag_engine.py         # pgvector semantic similarity search for client facts
│   │   ├── ram_fallback.py       # 100% in-memory rule engine (0ms fail-safe fallback)
│   │   ├── semantic_cache.py     # Redis Cloud vector & keyword cache
│   │   ├── session_store.py      # Active in-flight call state & speech statistics
│   │   ├── sip_client.py         # Asterisk WebRTC SIP client & audio stream transport
│   │   ├── sip_parser.py         # SIP protocol framing & SDP negotiation parser
│   │   ├── stt_engine.py         # Sarvam saaras:v3 & Deepgram Nova-2 streaming STT
│   │   ├── summarizer.py         # Post-call conversation analysis & lead grading
│   │   ├── tenant_service.py     # Multi-tenant DID resolver & configuration loader
│   │   ├── tts_engine.py         # Sarvam bulbul:v3, Edge-TTS, and Deepgram Aura TTS
│   │   └── voice_agent.py        # Master conversational orchestrator loop
│   ├── db/
│   │   ├── models.py             # SQLAlchemy models (tenants, calls, leads, billing, etc.)
│   │   ├── session.py            # Async & Sync PostgreSQL connection pools
│   │   └── init_db.py            # Database schema bootstrapper
│   └── routers/
│       ├── admin_portal.py       # Master Platform Owner dashboard & tenant management
│       ├── reseller_portal.py    # Reseller white-label portal & margin controls
│       ├── tenant_portal.py      # End-client lead management & analytics
│       ├── sip_stream.py         # WebSocket bridge for Asterisk / RP Digital SIP calls
│       ├── exotel_stream.py      # Exotel bi-directional 16-bit linear PCM audio stream
│       ├── exotel_voice.py       # Exotel Customer Connect & human escalation webhooks
│       └── simulator_api.py      # Browser-based test call simulator & audio debug tool
├── data/                         # Knowledge base documents and static audio assets
├── scripts/                      # DB migration, tenant activation, and seed scripts
├── static/                       # HTML5, CSS3, and JavaScript frontend dashboards
├── .env.example                  # Template configuration file
├── .gitignore                    # Production git ignore definitions
├── Dockerfile                    # Containerization specification
└── requirements.txt              # Production Python dependencies
```

---

## 🚀 Quick Start Guide

### 1. Prerequisites
* Python 3.9+ or Python 3.10+
* PostgreSQL with `pgvector` extension
* Redis instance (Local or Redis Cloud)
* SIP Provider (RP Digital / Asterisk) or Exotel Virtual Number

### 2. Environment Setup

Clone repository and activate virtual environment:
```bash
git clone <your-repo-url>
cd QwickCall
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure `.env` File

Copy the template:
```bash
cp .env.example .env
```

Configure the essential keys:
```env
# Server
PORT=8001
HOST=0.0.0.0
BASE_URL=https://your-public-domain.com

# Database (PostgreSQL + pgvector)
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/dbname
SYNC_DATABASE_URL=postgresql://user:password@host:5432/dbname
REDIS_URL=redis://default:password@host:port

# AI Engines
GROQ_API_KEY=gsk_...
GEMINI_API_KEY=AIzaSy...
SARVAM_API_KEY=sk_...
STT_ENGINE=sarvam
TTS_ENGINE=sarvam
SARVAM_MODEL=bulbul:v3
SARVAM_SPEAKER=ritu

# RP Digital / Asterisk SIP
RPDIGITAL_ASTERISK_HOST=kenyavoice.rpdigitalphone.com
RPDIGITAL_ASTERISK_PORT=5000
RPDIGITAL_ASTERISK_PATH=/ws
RPDIGITAL_SIP_USER=your_sip_extension
RPDIGITAL_SIP_PASSWORD=your_sip_password
RPDIGITAL_DID_NUMBER=+91XXXXXXXXXX

# Economics (INR)
DEFAULT_WHOLESALE_RATE_PER_MIN=3.00
DEFAULT_DIRECT_RETAIL_RATE_PER_MIN=5.00
PLATFORM_BASE_COST_PER_MIN=2.00
```

### 4. Database Initialization

Run database initialization to create all tables, indexes, and pgvector extensions:
```bash
python3 -c "from app.db.init_db import init_database; init_database()"
```

### 5. Launch the Server

Run via Uvicorn:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

---

## 🖥️ Portals & Interfaces

* **Web Call Simulator:** `http://localhost:8001/`  
  *(Test live mic-to-agent conversational voice with sub-second turnaround without spending phone airtime).*
* **Admin Dashboard:** `http://localhost:8001/admin`  
  *(Monitor active calls, view provider-level raw operational costs, manage tenants, and track platform profits).*
* **Reseller Portal:** `http://localhost:8001/reseller`  
  *(White-label portal for wholesale partners to manage client rates and margin withdrawals).*
* **API Documentation:** `http://localhost:8001/docs` (Interactive Swagger UI).

---

## 🔒 Security & Guardrails

1. **Jailbreak & Roleplay Shield:** Hardcoded guardrails prevent the AI from roleplaying outside of its approved business persona or revealing internal prompts.
2. **Hallucination Prevention:** Course fees, timings, refund policies, and placement records are grounded strictly in vectorized PDF knowledge chunks.
3. **Graceful Escalation:** When a caller requests a manager, human counselor, or becomes hostile, the agent immediately bridges the call to `DEFAULT_ESCALATION_NUMBER`. If the line is busy, it captures the caller's query as a high-priority lead in the database.

---

## 📄 License
Proprietary & Confidential — Built for QwickCall Enterprise Deployments.
# calling-agent
