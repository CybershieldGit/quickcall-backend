# QwickCall — Enterprise AI Calling Agent & Omnichannel Platform 🎙️

An ultra-low latency, multi-tenant AI voicebot platform engineered for high-concurrency inbound and outbound phone calls on Indian telecom infrastructure.

QwickCall is built for real-world enterprise conversations (admissions, customer care, sales qualification, appointment booking) with native support for **bilingual English & Hindi/Hinglish**, **natural barge-in interruption**, **ambient office acoustics**, and **instant omnichannel follow-up via WhatsApp and Email**.

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
        DIDRouter["Tenant DID Resolution<br/>(Resolves DID → Tenant Agent & KB)"]
        VAD["VAD & Barge-In Handler<br/>(Stops playback on caller speech)"]
        
        subgraph STT ["Speech-to-Text (STT)"]
            SarvamSTT["Sarvam AI saaras:v3 (Primary)<br/>Native Indian Multilingual"]
            DeepgramSTT["Deepgram Nova-2 (Fallback)"]
        end
        
        subgraph Brain ["Intelligence & Reasoning (LLM)"]
            Cache["Semantic Cache<br/>(In-Memory Vector Cache: <20ms, ₹0.00)"]
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

### 1. Ultra-Low Latency Conversational Turnaround (~350ms – 500ms)
* **True Streaming Pipeline:** STT streams raw 16-bit 8000Hz PCM directly into sentence-boundary streaming LLM generation, feeding live audio frames into streaming decoders without waiting for full turn completion.
* **Pre-Cached Static Greetings:** Standard greetings (*"Namaste! iDigitalStudies mein aapka swagat hai..."*) are synthesized at startup and cached in RAM for **0ms initial response time and 0 API cost**.
* **Direct Streaming Mode:** Canned filler words can be disabled (`ENABLE_CONVERSATIONAL_FILLERS=false`) to eliminate awkward artificial affirmations, allowing the agent to respond directly in natural sub-450ms cadence.

### 2. Multi-Tier Self-Healing LLM Fallback (Zero Dead-Air)
Calls never drop or stall due to upstream provider rate limits:
* **Tier 1 (Primary):** **Groq LPU** (`llama-3.3-70b-versatile` / `qwen/qwen3.8-27b`) delivering <250ms TTFT.
* **Tier 2 (Secondary Fallback):** **Google Gemini Flash Lite** via direct HTTP REST & Server-Sent Events (SSE).
* **Tier 3 (Zero-Network RAM Fallback):** In-memory regex & intent matcher that answers core operational questions (fees, timings, syllabus, placements) with 0 network calls and 0ms latency.

### 3. Native Indian Multilingual Support (English & Hindi/Hinglish)
* Automatic lexical and script detection.
* The agent seamlessly speaks colloquial Hinglish (*"Ji bilkul, main aapko batch timings aur placement support ke baare mein batati hoon..."*) or professional English based on caller choice.

### 4. Natural VAD Barge-In Interruption
* Detects when the human speaks while the agent is talking.
* Instantly drops audio playback buffers on the SIP/WebSocket channel so the caller never talks over the agent.

### 5. Office Acoustic Simulation
* Optional acoustic DSP filter ([app/core/audio/audio_utils.py](file:///Users/CST_TECH/Desktop/callingagent/QwickCall/app/core/audio/audio_utils.py)) applying call-center headset EQ, subtle desk early-reflections, and faint background office chatter to eliminate the artificial "sterile robot" voice feel.

### 6. Isolated Multi-Tenant & Multi-Agent Architecture
* **Specialized AI Agents:** Tenants can configure multiple specialized AI agents (e.g. Admissions, Placements, Support) sharing a unified pgvector knowledge base.
* **Dynamic DID Routing:** Each inbound virtual number automatically binds to its assigned AI agent, persona, knowledge base, human escalation phone number, and custom guardrails. Switch agents per phone line with 1 click.
* **Dual-Wallet Margins:** Real-time billing transactions track Client Retail Rate (e.g. ₹5.00/min), Reseller Wholesale Rate (e.g. ₹3.00/min), and Platform Operational Cost.

---

## 🔒 Production Hardening & Guardrails (UAT Ready)

1. **Cryptographically Signed HS256 JWT Authentication:**
   * Strict stateless JWT validation on all `/api/v1/admin/*`, `/api/v1/reseller/*`, and `/api/v1/tenant/*` endpoints.
   * No hardcoded token bypasses. Tenant isolation enforced by verifying active database tenant records against JWT claims.

2. **SSRF Sandbox Defense:**
   * Webhook and external asset URLs are validated through `validate_and_sanitize_url()`.
   * Prohibits private CIDR blocks (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), link-local metadata addresses (`169.254.169.254`), and loopback interfaces (`127.0.0.1`).

3. **Pre-Call Financial Wallet Safeguards:**
   * Outbound calls reject with `HTTP 402 Payment Required` if the tenant's wallet balance is $< ₹15.00$.
   * Inbound SIP/Exotel streams check balance on ingress and gracefully reject with an automated notification audio prompt if balance $< ₹10.00$, preventing unbilled airtime leaks.

4. **TTS Memory Bounded Cache (LRU):**
   * Audio chunk cache capped at `MAX_TTS_CACHE_ENTRIES = 300` using an `OrderedDict` LRU eviction strategy to prevent unbounded memory growth during long-running production sessions.

5. **Graceful Human Escalation:**
   * When a caller requests a manager, human supervisor, or becomes hostile, the agent immediately initiates a SIP REFER transfer to `DEFAULT_ESCALATION_NUMBER`. If busy, it captures the inquiry as a high-priority lead in PostgreSQL.

---

## 💰 Operational Economics & Real-Time Costing

### Standalone Voice Calling Cost (Per Call Minute)
Because conversations follow a **50/50 turn-taking model** (the AI only speaks for ~25–30 seconds per 60-second call), actual runtime costs stay well below budget limits:

| Component | Provider & Model | Live Commercial Rate | Effective Cost / Minute |
| :--- | :--- | :--- | :--- |
| **Carrier Airtime** | **RP Digital SIP Trunk** | Flat Monthly DID Plan | **₹0.000 / min** |
| **Speech-to-Text (STT)** | **Sarvam AI `saaras:v3`** | ₹30 / hour of audio | **₹0.183 / min** *(~22s caller speech)* |
| **LLM Inference** | **Groq Llama 3.3 70B** | $0.59 / $0.79 per 1M tokens | **₹0.120 / min** *(with Semantic Cache)* |
| **Text-to-Speech (TTS)** | **Sarvam AI `bulbul:v3`** | ₹30 per 10,000 characters | **₹1.050 / min** *(~350 chars spoken)* |
| **Static Greetings** | **In-Memory RAM Cache** | Pre-warmed at startup | **₹0.000 / min** |
| **TOTAL VARIABLE OP COST** | | | **~₹1.35 / minute** |

> **Under ₹1.80/min Limit:** Realistic phone calls stay safely between **₹1.00 and ₹1.45/min**. If using **Microsoft Edge-TTS** (`TTS_ENGINE=edge-tts`), variable cost drops to **₹0.30 / minute**.

### Omnichannel Action Add-On (WhatsApp & Email Dispatch)
When a caller asks for a brochure or syllabus over the phone, the AI dispatches it asynchronously:

| Action | Provider | Unit Cost | Call Latency Added |
| :--- | :--- | :--- | :--- |
| **WhatsApp Brochure PDF** | Meta Cloud API / Twilio | **₹0.135** *(Meta ₹0.115 + LLM ₹0.02)* | **0ms** *(Async background task)* |
| **Email Syllabus PDF** | Gmail SMTP / SendGrid | **₹0.030** *(LLM formatting only)* | **0ms** *(Async background task)* |

---

## 📁 Clean Modular Directory Structure

```
QwickCall/
├── app/
│   ├── config.py                 # Central platform settings & environment variables
│   ├── main.py                   # FastAPI server, lifecycle warmup, routing registration
│   ├── api/                      # Clean API Presentation Layer
│   │   ├── router.py             # Consolidated v1 API master router
│   │   └── v1/
│   │       ├── telephony/        # SIP WebRTC, Exotel Stream, Outbound Calling
│   │       ├── chat/             # Conversational Web Chat & WhatsApp Webhook
│   │       ├── portals/          # Tenant, Admin, Reseller Portals
│   │       ├── analytics/        # Telemetry, Costs & Reporting
│   │       └── simulator/        # Audio / Text Simulation Sandbox
│   ├── core/                     # Cross-Cutting Core
│   │   ├── auth.py               # HS256 JWT generation, verification & RBAC guards
│   │   ├── session_store.py      # Active in-flight call state & speech statistics
│   │   └── audio/                # PCM resampling, RMS energy, Office Acoustics, SIP Client
│   ├── services/                 # Domain Business Logic
│   │   ├── voice_agent/          # Conversational State Machine, Intent Detection, Fillers
│   │   ├── ai/                   # Multi-Tier LLM, Multilingual STT & TTS Providers
│   │   ├── knowledge/            # pgvector RAG Engine, PDF Processor, Semantic Cache
│   │   ├── messaging/            # Omnichannel Brochure Delivery (WhatsApp & Email)
│   │   ├── tenant/               # Multi-Tenancy, Wallet Guards & DID Routing
│   │   └── telemetry/            # Call Logging, Live Telemetry & Executive Summarizer
│   └── db/                       # Persistence & Data Access
│       ├── models.py             # SQLAlchemy Database Models
│       ├── session.py            # Async & Sync PostgreSQL Engine Pools
│       └── init_db.py            # Database Schema Bootstrapper & Seeder
├── data/                         # Knowledge base documents and static audio assets
├── scripts/                      # PDF ingestion & tenant onboarding scripts
├── static/                       # HTML5, CSS3, and JavaScript frontend dashboards
│   ├── index.html                # Unified Client Workspace & Real-time Call Lab
│   ├── whatsapp.html             # Interactive WhatsApp Webhook Simulator
│   ├── css/                      # Modular Design System & Theme Styles
│   └── js/                       # Workspace, Admin, Reseller & Chat Controllers
├── tests/                        # Comprehensive Automated Test Suites
├── .env.example                  # Template configuration file
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
JWT_SECRET=your_super_secret_jwt_key_here

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

# Conversational Fillers Toggle (false = direct sub-400ms streaming)
ENABLE_CONVERSATIONAL_FILLERS=false

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

## 🧪 Comprehensive Automated Test Suites

Run the test suite across all architectural layers:

```bash
# 1. Authentication, SSRF Sandbox, Pre-Call Wallet & TTS Cache Bounds
python3 tests/test_auth_and_security.py

# 2. Anti-Trolling, Graduated Deflection & Multi-Tenant Isolation
python3 tests/test_guardrails_and_tenants.py

# 3. Dual-Channel Voice & Chat RAG Execution
python3 tests/test_voice_and_chat.py

# 4. Clean Modular Import & Architecture Verification
python3 tests/test_structure.py

# 5. Latency & Sub-20ms In-Memory Cache Performance Validation
python3 tests/test_performance.py
```

---

## 🖥️ Portals & Interfaces

* **Client Workspace & Call Lab:** `http://localhost:8001/`  
  *(Manage AI Agents, hot-switch DIDs, test live voice reasoning in browser, and view lead CRM).*
* **Admin Dashboard:** `http://localhost:8001/admin`  
  *(Monitor active calls, view provider-level raw operational costs, manage tenants, and track platform profits).*
* **Reseller Portal:** `http://localhost:8001/reseller`  
  *(White-label portal for wholesale partners to manage client rates and margin withdrawals).*
* **WhatsApp Chatbot Sandbox:** `http://localhost:8001/whatsapp`  
  *(Test live multi-turn WhatsApp conversation flow and instant syllabus PDF brochure dispatch).*
* **API Documentation:** `http://localhost:8001/docs` *(Interactive OpenAPI / Swagger UI)*.

---

## 📄 License
Proprietary & Confidential — Built for QwickCall Enterprise Deployments.
