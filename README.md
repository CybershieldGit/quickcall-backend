# QwickCall — Enterprise Multi-Tenant AI Voicebot & Omnichannel Platform 🎙️

[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.9%20%7C%203.10%20%7C%203.11-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-pgvector-4169E1?logo=postgresql&logoColor=white)](https://github.com/pgvector/pgvector)
[![Redis](https://img.shields.io/badge/Redis-Semantic%20Cache-DC382D?logo=redis&logoColor=white)](https://redis.io)
[![License](https://img.shields.io/badge/License-Proprietary-red)](#-license)

**QwickCall** is an ultra-low latency, production-hardened, multi-tenant AI voicebot and conversational telephony platform engineered for high-concurrency inbound and outbound calls on Indian and global telecom networks.

It bridges legacy SIP and cloud telephony ([Asterisk](file:///Users/CST_TECH/Desktop/callingagent/QwickCall/app/core/audio/sip_client.py), [Exotel](file:///Users/CST_TECH/Desktop/callingagent/QwickCall/app/api/v1/telephony/exotel_stream.py)) with real-time speech intelligence ([Sarvam AI](file:///Users/CST_TECH/Desktop/callingagent/QwickCall/app/services/ai/tts_engine.py), [Deepgram](file:///Users/CST_TECH/Desktop/callingagent/QwickCall/app/services/ai/stt_engine.py), [Groq LPU](file:///Users/CST_TECH/Desktop/callingagent/QwickCall/app/services/ai/llm_service.py), [Google Gemini](file:///Users/CST_TECH/Desktop/callingagent/QwickCall/app/services/ai/llm_service.py)) and asynchronous omnichannel delivery via WhatsApp and Email.

---

## 🏗️ High-Level System Architecture

```mermaid
flowchart TD
    Caller([📞 Caller / Customer Phone Line])

    subgraph TelephonyLayer ["1. Telephony Ingestion Layer"]
        RPDigital["RP Digital Asterisk SIP Trunk<br/>(WebSocket WebRTC @ ₹0.00 Airtime)"]
        Exotel["Exotel Cloud Telephony<br/>(Virtual DID / Passthru / Media Streams)"]
    end

    Caller <==>|"16-bit Linear PCM (8kHz / 16kHz Mono)"| TelephonyLayer

    subgraph CoreEngine ["2. QwickCall Real-Time Processing Engine"]
        DIDRouter["Tenant & Agent DID Resolution<br/>(DID → Tenant → Agent Persona & KB)"]
        VAD["VAD & Barge-In Handler<br/>(Instant audio drop upon caller interruption)"]
        
        subgraph STTLayer ["Speech-to-Text (STT)"]
            SarvamSTT["Sarvam AI saaras:v3 (Primary)<br/>Native Indian Multilingual & Hinglish"]
            DeepgramSTT["Deepgram Nova-2 (Fallback / Direct)"]
        end

        subgraph BrainLayer ["Intelligence & Reasoning (Tri-Tier LLM)"]
            Cache["Redis Dynamic Semantic Cache<br/>(&lt;20ms Latency, Cosine Similarity, ₹0.00)"]
            GroqLLM["Tier 1: Groq LPU<br/>(Llama 3.3 70B / Qwen 27B: &lt;250ms TTFT)"]
            GeminiLLM["Tier 2: Gemini Flash Lite<br/>(Direct HTTP REST / SSE Fallback)"]
            RAMLLM["Tier 3: In-Memory RAM Engine<br/>(Zero-Network Offline Fall-safe)"]
            RAG["pgvector RAG Knowledge Base<br/>(Company Docs, SKU Catalogs, FAQs)"]
        end

        subgraph TTSLayer ["Text-to-Speech (TTS)"]
            RAMCache["RAM Pre-Cached Greetings<br/>(0ms Latency, 0 API Cost)"]
            SarvamTTS["Sarvam AI bulbul:v3<br/>(Indian English / Hinglish: Ritu)"]
            DeepgramTTS["Deepgram Aura TTS<br/>(Asteria, Luna, Stella)"]
            EdgeTTS["Microsoft Edge-TTS<br/>(Streaming Neural: ₹0.00 / min)"]
            ElevenLabsTTS["ElevenLabs (Optional)"]
        end

        Acoustics["Office Acoustic Engine<br/>(Headset EQ + Subtle Room Ambience)"]
    end

    TelephonyLayer <--> DIDRouter
    DIDRouter <--> VAD
    VAD --> STTLayer
    STTLayer --> BrainLayer
    BrainLayer <--> RAG
    BrainLayer --> TTSLayer
    TTSLayer --> Acoustics
    Acoustics --> TelephonyLayer

    subgraph AddonLayer ["3. Asynchronous Omnichannel Dispatch"]
        IntentCheck{"Caller requests<br/>Brochure / Syllabus?"}
        MetaWA["Meta WhatsApp Cloud API / Twilio<br/>(Sends PDF Brochure / Fees)"]
        EmailSMTP["SMTP Gmail / SendGrid<br/>(Sends Official Curriculum PDF)"]
    end

    BrainLayer -.->|Non-Blocking Async Event| IntentCheck
    IntentCheck -->|WhatsApp Action| MetaWA
    IntentCheck -->|Email Action| EmailSMTP
```

---

## ⚡ Core Platform Capabilities

### 1. Ultra-Low Conversational Latency (~350ms – 500ms)
* **True Audio Streaming:** STT streams raw 16-bit 8000Hz PCM directly into sentence-boundary streaming LLM generation, feeding live audio frames into streaming decoders without waiting for full turn completion.
* **Pre-Cached Static Greetings:** Standard tenant greetings (*"Hello, I am Aanya from iDigital Studies..."*) are synthesized at startup and cached in RAM for **0ms initial turnaround and 0 API cost**.
* **Direct Streaming Mode:** Canned filler words can be disabled via `ENABLE_CONVERSATIONAL_FILLERS=false` to eliminate awkward artificial affirmations, allowing the agent to respond directly with human-like timing.

### 2. Tri-Tier Self-Healing LLM Engine (Zero Dead-Air)
Calls never drop or stall due to upstream provider outages or rate limits:
* **Tier 1 (Primary):** [Groq LPU](file:///Users/CST_TECH/Desktop/callingagent/QwickCall/app/services/ai/llm_service.py) (`llama-3.3-70b-versatile` or `qwen/qwen3.8-27b`) delivering <250ms TTFT.
* **Tier 2 (Secondary Fallback):** [Google Gemini Flash](file:///Users/CST_TECH/Desktop/callingagent/QwickCall/app/services/ai/llm_service.py) via direct HTTP REST & Server-Sent Events (SSE).
* **Tier 3 (Zero-Network RAM Fallback):** [In-Memory RAM Engine](file:///Users/CST_TECH/Desktop/callingagent/QwickCall/app/services/ai/ram_fallback.py) using regex and intent matching to answer core operational questions (fees, timings, syllabus, placements) with 0 network calls and 0ms latency.

### 3. Redis Dynamic Semantic Cache (<20ms, ₹0.00 Token Cost)
* [Semantic Cache](file:///Users/CST_TECH/Desktop/callingagent/QwickCall/app/services/knowledge/semantic_cache.py) indexes frequent question-answer pairs using `SentenceTransformer` (`all-MiniLM-L6-v2`) embeddings.
* Automatically serves pre-verified answers when semantic similarity $\ge 0.90$, dropping LLM latency to **under 25ms** while bypassing upstream LLM token costs.
* Critical topics (fees, refunds, financial promises, complaints) are safely excluded from caching by policy.

### 4. Native Indian Multilingual & Hinglish Support
* Automatic lexical and script detection ([app/services/voice_agent/agent.py](file:///Users/CST_TECH/Desktop/callingagent/QwickCall/app/services/voice_agent/agent.py)).
* Seamlessly code-switches between colloquial Hinglish (*"Ji bilkul, main aapko batch timings aur placement support ke baare mein batati hoon..."*) and professional English based on caller preference.

### 5. Multi-Agent Dynamic DID Routing
* **Multiple Specialized AI Personas:** A single tenant can deploy multiple AI agents (e.g. *Aanya - Admissions Counselor* and *Rohan - Placement & Career Advisor*) sharing a unified knowledge base.
* **Instant Phone Line Binding:** Inbound DIDs automatically bind to designated agents with customized personas, greetings, voices, and human escalation numbers. Phone lines can be reassigned with 1 click in the dashboard.

### 6. Natural Barge-In Interruption (VAD)
* Detects when the human speaks while the agent is talking.
* Instantly drops audio playback queues on the SIP or Exotel WebSocket channel so the caller never talks over the agent.

### 7. Asynchronous Omnichannel Dispatch
* When callers ask for brochures, syllabi, fee structures, or curriculum over the phone, [Brochure Service](file:///Users/CST_TECH/Desktop/callingagent/QwickCall/app/services/messaging/brochure_service.py) parses spoken contact details ("double nine eight seven...", "name at gmail dot com"), provides phonetic TTS readout confirmation, and dispatches PDFs via **WhatsApp** (Meta Cloud / Twilio) or **Email** (SMTP / SendGrid) asynchronously without stalling the voice call.

### 8. Office Acoustic & Headset DSP Filter
* Optional DSP simulation ([app/core/audio/audio_utils.py](file:///Users/CST_TECH/Desktop/callingagent/QwickCall/app/core/audio/audio_utils.py)) applying call-center headset EQ, subtle room reflections, and faint background office chatter to eliminate the artificial "sterile robot" voice feel.

---

## 🔒 Security, Guardrails & Billing Safeguards

1. **Cryptographically Signed HS256 JWT Authentication:**
   * Strict stateless JWT validation on all `/api/v1/admin/*`, `/api/v1/reseller/*`, and `/api/v1/tenant/*` endpoints ([app/core/auth.py](file:///Users/CST_TECH/Desktop/callingagent/QwickCall/app/core/auth.py)).
   * Role-based access control (`superadmin`, `reseller`, `tenant`).
2. **SSRF Sandbox Defense:**
   * External webhook and website-scraping URLs are validated through `validate_and_sanitize_url()`.
   * Prohibits private CIDR blocks (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), AWS/Cloud metadata (`169.254.169.254`), and loopback (`127.0.0.1`).
3. **Pre-Call Financial Wallet Safeguards:**
   * Outbound calls reject with `HTTP 402 Payment Required` if the tenant's wallet balance is $< ₹15.00$.
   * Inbound SIP/Exotel streams check balance on ingress and gracefully reject with an automated notification audio prompt if balance $< ₹10.00$, preventing unbilled airtime leaks.
4. **Graduated Deflection & Anti-Trolling:**
   * Multi-turn deflection against off-topic queries, offensive remarks, or prompt injections without breaking character.
5. **Dual-Wallet & Margin Telemetry:**
   * Real-time billing transactions track Client Retail Rate (e.g. ₹5.00/min), Reseller Wholesale Rate (e.g. ₹3.00/min), and Platform Operational Cost (e.g. ₹2.00/min).

---

## 💰 Operational Economics & Cost Breakdown

### Standalone Voice Calling Cost (Per Call Minute)
Conversations follow a **50/50 turn-taking model** (the AI only speaks for ~25–30 seconds per 60-second call):

| Component | Provider & Model | Live Commercial Rate | Effective Cost / Minute |
| :--- | :--- | :--- | :--- |
| **Carrier Airtime** | **RP Digital Asterisk SIP** | Flat Monthly Trunk Plan | **₹0.000 / min** |
| **Speech-to-Text (STT)** | **Sarvam AI `saaras:v3`** | ₹30 / hour of audio | **₹0.183 / min** *(~22s caller speech)* |
| **LLM Inference** | **Groq Llama 3.3 70B** | $0.59 / $0.79 per 1M tokens | **₹0.120 / min** *(with Semantic Cache)* |
| **Text-to-Speech (TTS)** | **Sarvam AI `bulbul:v3`** | ₹30 per 10,000 characters | **₹1.050 / min** *(~350 chars spoken)* |
| **Static Greetings** | **In-Memory RAM Cache** | Pre-warmed at startup | **₹0.000 / min** |
| **TOTAL VARIABLE OP COST** | | | **~₹1.35 / minute** |

> **Cost Optimization:** Using **Microsoft Edge-TTS** (`TTS_ENGINE=edge-tts`), the total variable operating cost drops to **₹0.30 / minute**.

### Omnichannel Action Add-On (WhatsApp & Email Dispatch)
| Action | Provider | Unit Cost | Call Latency Added |
| :--- | :--- | :--- | :--- |
| **WhatsApp Brochure PDF** | Meta Cloud API / Twilio | **₹0.135** *(Meta ₹0.115 + LLM ₹0.02)* | **0ms** *(Async background task)* |
| **Email Syllabus PDF** | Gmail SMTP / SendGrid | **₹0.030** *(LLM formatting only)* | **0ms** *(Async background task)* |

---

## 🖥️ Portals & UI Navigation Matrix

The frontend suite is built with a refined, dark-mode executive aesthetic (Linear / Vercel style) and maps to distinct role-based interfaces:

| URL Route | Interface | Description |
| :--- | :--- | :--- |
| `/` or `/tenant` or `/client` | **Client Business Workspace & Call Lab** (`tenant.html`) | Manage multi-agent personas, assign DIDs, upload PDF knowledge bases, crawl websites, test live in-browser voice calls with real-time waveform visualizers, view transcripts, and manage lead CRM. |
| `/admin` | **Platform SuperAdmin Portal** (`admin.html`) | Executive financial telemetry (gross revenue, telco/AI cost, net margin), wholesale reseller partner management, DID number pool inventory, direct enterprise client billing. |
| `/reseller` | **Wholesale Reseller Portal** (`reseller.html`) | White-label branding setup (custom logo, portal title, support email), sub-client portfolio management, retail rate markup controls, wholesale wallet credit monitoring. |
| `/login` or `/auth` or `/signin` | **Unified Authentication Portal** (`login.html`) | Secure JWT authentication with role-based automatic routing to Admin, Reseller, or Tenant workspaces. |
| `/chats` or `/chat` or `/whatsapp` | **WhatsApp Chatbot Studio** (`whatsapp.html`) | Interactive multi-turn WhatsApp conversation sandbox testing real-time intent detection and PDF brochure dispatch. |
| `/simulator` or `/docs-portal` | **Call & API Sandbox** (`index.html`) | Developer sandbox for text/audio turns, DID resolution testing, TTS voice switching, and cache verification. |
| `/docs` & `/redoc` | **OpenAPI Interactive Documentation** | Auto-generated Swagger & ReDoc API specifications. |

---

## 📁 Clean Modular Directory Structure

```
QwickCall/
├── app/
│   ├── config.py                 # Central platform settings & environment variables
│   ├── main.py                   # FastAPI server, lifecycle warmup, routing registration
│   ├── api/                      # Clean API Presentation Layer
│   │   ├── router.py             # Consolidated v1 master router
│   │   └── v1/
│   │       ├── telephony/        # Exotel Voice/Stream, Asterisk SIP Stream, Outbound
│   │       ├── chat/             # Web Chat Agent & WhatsApp Cloud Webhooks
│   │       ├── portals/          # Tenant Workspace, Admin & Reseller Portals
│   │       ├── analytics/        # Real-time Telemetry, System Stats & Cost Analytics
│   │       └── simulator/        # Audio / Text Simulation Sandbox & TTS switcher
│   ├── core/                     # Cross-Cutting Infrastructure
│   │   ├── auth.py               # HS256 JWT generation, password hashing & RBAC guards
│   │   ├── session_store.py      # Distributed active call sessions & speech statistics
│   │   └── audio/                # Asterisk SIP Client, SIP Parser, PCM Resampling, EQ & Acoustics
│   ├── services/                 # Domain Business Logic
│   │   ├── ai/                   # Multi-Tier LLM, STT & TTS Providers, RAM Fallback
│   │   ├── knowledge/            # pgvector RAG Engine, PDF Processor, Redis Semantic Cache
│   │   ├── messaging/            # Omnichannel Brochure Delivery (WhatsApp & Email)
│   │   ├── telemetry/            # Call Logging, In-Flight Monitoring & Executive Summarizer
│   │   ├── tenant/               # Multi-Tenancy, Wallet Guards & DID Routing
│   │   └── voice_agent/          # Conversational State Machine, Intent Detector, Fillers
│   └── db/                       # Persistence & Data Access Layer
│       ├── models.py             # SQLAlchemy Models (Resellers, Tenants, Agents, Calls, Leads, etc.)
│       ├── session.py            # Async (asyncpg) & Sync (psycopg2) Engine Pools
│       └── init_db.py            # Database Schema Bootstrapper, Migrations & Seeders
├── data/                         # Knowledge base PDFs and static audio assets
├── scripts/
│   ├── activate_phonotel_tenant.py     # Script to configure multi-tenant routing & personas
│   └── ingest_official_pdf_knowledge.py # Ingest official 21-page PDF knowledge into pgvector
├── static/                       # Production HTML5/CSS3/JavaScript Dashboards
│   ├── tenant.html               # Client Business Workspace & Call Lab
│   ├── admin.html                # Platform SuperAdmin & Financial Dashboard
│   ├── reseller.html             # White-label Reseller Partner Portal
│   ├── login.html                # Unified Login Portal
│   ├── whatsapp.html             # WhatsApp Chatbot Studio
│   ├── index.html                # Developer Sandbox & Call Simulator
│   ├── css/                      # Modular Dark-Mode Design System & Theme Styles
│   └── js/                       # Portal Controllers, WebRTC Audio & Waveform Renderers
├── tests/                        # Comprehensive Automated Test Suites
│   ├── test_auth_and_security.py       # Auth, SSRF Sandbox, Wallet & TTS LRU Cache Bounds
│   ├── test_guardrails_and_tenants.py  # Anti-Trolling, Graduated Deflection & Tenant Isolation
│   ├── test_performance.py            # Sub-20ms Redis Cache & Voice Agent Turn Latency
│   ├── test_structure.py              # Modular Clean Architecture & Import Integrity
│   └── test_voice_and_chat.py         # Dual-Channel Voice & Chat RAG Execution
├── make_call.py                  # CLI tool to trigger outbound Exotel AI calls
├── Dockerfile                    # Containerization specification
├── requirements.txt              # Production Python dependencies
├── .env.example                  # Template configuration file
└── README.md                     # Comprehensive Platform Documentation
```

---

## 📡 API Reference & WebSocket Endpoints

### 1. Telephony WebSockets & Inbound Streams
| Method / Protocol | Path | Description |
| :--- | :--- | :--- |
| `WEBSOCKET` | `/api/v1/sip/stream` | Full-duplex Asterisk WebRTC SIP audio streaming (16-bit 8000Hz PCM) with live VAD and barge-in. |
| `POST` | `/api/v1/sip/reconnect` | Triggers a fresh SIP registration attempt for the Asterisk client. |
| `GET` | `/api/v1/sip/status` | Returns active Asterisk SIP client connection and registration status. |
| `WEBSOCKET` | `/api/v1/exotel/stream` | Exotel bidirectional media stream (8000Hz $\mu$-law / PCM). |
| `POST` | `/api/v1/voicebot-connect` | Exotel Applet webhook returning Passthru / Stream JSON/XML payload. |
| `POST` | `/api/v1/passthru` | Exotel Passthru applet callback URL. |
| `POST` | `/api/v1/status` | Exotel call lifecycle status callback (`busy`, `completed`, `failed`). |
| `POST` | `/api/v1/transfer-callback` | Handles human supervisor SIP REFER / PSTN transfer completions. |

### 2. Outbound Calling
| Method | Path | Description |
| :--- | :--- | :--- |
| `POST` | `/api/v1/outbound` | Initiates an outbound AI call to a destination phone number via Exotel. Requires tenant JWT. |
| `GET` | `/api/v1/recent` | Retrieves recent calls, durations, transcripts, and executive summaries. |
| `GET` | `/api/v1/active-calls` | Fetches active in-flight calls and active speaking durations. |
| `GET` | `/api/v1/{call_id}/transcript` | Fetches the full turn-by-turn transcript for a specific call. |

### 3. Conversational Web Chat & WhatsApp
| Method | Path | Description |
| :--- | :--- | :--- |
| `POST` | `/api/v1/chat/start` | Initializes a chat session and returns the tenant's localized greeting. |
| `POST` | `/api/v1/chat/message` | Sends a message to the AI chat counselor with full RAG retrieval. |
| `POST` | `/api/v1/chat/stream` | Streams LLM responses via Server-Sent Events (SSE). |
| `GET` | `/api/v1/chat/history/{session_id}` | Retrieves conversation history for a given chat session. |
| `DELETE` | `/api/v1/chat/history/{session_id}` | Clears conversation history for a chat session. |
| `POST` | `/api/v1/chat/send-brochure` | Manually triggers brochure delivery via WhatsApp or Email. |
| `GET` | `/api/v1/whatsapp/webhook` | Meta Cloud API webhook verification challenge (`hub.challenge`). |
| `POST` | `/api/v1/whatsapp/webhook` | Meta Cloud API inbound WhatsApp message webhook processor. |
| `GET` | `/api/v1/whatsapp/status` | Returns WhatsApp integration status and configured provider. |

### 4. Tenant Business Workspace (`/api/v1/tenant/*`)
*All endpoints require Bearer JWT token with `tenant` or `superadmin` role.*
| Method | Path | Description |
| :--- | :--- | :--- |
| `POST` | `/api/v1/tenant/register` | Self-serve or reseller-guided tenant registration. |
| `POST` | `/api/v1/tenant/login` | Tenant authentication via ID/email and password. Returns JWT token. |
| `GET` | `/api/v1/tenant/details/{tenant_id}` | Fetches tenant profile, balance, active DIDs, and assigned agents. |
| `POST` | `/api/v1/tenant/upload-knowledge-pdf` | Uploads and embeds company or catalog PDF documents into pgvector. |
| `POST` | `/api/v1/tenant/scrape-website` | Crawls and ingests website content into tenant knowledge base. |
| `GET` | `/api/v1/tenant/knowledge` | Lists all indexed knowledge chunks for the tenant. |
| `POST` | `/api/v1/tenant/telephony-settings` | Updates telephony mode (master pool vs. BYOC Exotel credentials). |
| `GET` | `/api/v1/tenant/available-numbers` | Lists available virtual phone numbers in the platform inventory. |
| `POST` | `/api/v1/tenant/assign-number` | Assigns an inventory DID to the tenant. |
| `POST` | `/api/v1/tenant/unassign-number` | Releases an assigned DID back to the available pool. |
| `GET` | `/api/v1/tenant/{tenant_id}/agents` | Lists all specialized AI agents configured for the tenant. |
| `POST` | `/api/v1/tenant/{tenant_id}/agents` | Creates a new AI agent (name, persona, greeting, language, escalation). |
| `PUT` | `/api/v1/tenant/{tenant_id}/agents/{agent_id}` | Updates an existing AI agent configuration. |
| `DELETE` | `/api/v1/tenant/{tenant_id}/agents/{agent_id}` | Deletes an AI agent. |
| `POST` | `/api/v1/tenant/assign-agent-to-did` | Hot-swaps which AI agent handles inbound calls on a specific DID. |
| `GET` | `/api/v1/tenant/leads` | Fetches captured prospective student/customer leads with sentiment. |
| `POST` | `/api/v1/tenant/leads` | Manually captures or updates a lead record. |
| `POST` | `/api/v1/tenant/leads/{lead_id}/status` | Updates lead follow-up status (`hot`, `warm`, `converted`, `cold`). |

### 5. SuperAdmin & Reseller Portals
* `/api/v1/admin/overview`: Platform-wide airtime, raw provider infrastructure cost, gross revenue, net margin.
* `/api/v1/admin/resellers`: Create, list, configure wholesale rate per minute (₹), and top-up reseller prepaid balances.
* `/api/v1/admin/telephony-pool`: Manage virtual phone number inventory (+91 DIDs and SIP extensions).
* `/api/v1/reseller/overview`: White-label reseller margin dashboard, client count, wholesale balance.
* `/api/v1/reseller/clients`: Provision sub-clients, set per-client retail rate (₹), and manage client credits.
* `/api/v1/reseller/branding`: Configure white-label portal name, logo URL, and customer support email.

### 6. System Health & Diagnostics
* `GET /health`: Comprehensive health check reporting database pool status, Asterisk SIP registration, Redis Semantic Cache hit statistics, configured LLM/STT/TTS engines, and active voice models.

---

## 🚀 Quick Start Guide

### 1. Prerequisites
* Python 3.9+ or Python 3.10+
* PostgreSQL with `pgvector` extension (or local SQLite for lightweight testing)
* Redis instance (Local or Redis Cloud)
* SIP Provider credentials (RP Digital Asterisk) or Exotel Virtual Number

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

Copy the template configuration:
```bash
cp .env.example .env
```

Set your core credentials in `.env`:
```env
# Server
PORT=8001
HOST=0.0.0.0
BASE_URL=https://your-public-domain.com
JWT_SECRET_KEY=your_super_secret_jwt_key_here

# Database (PostgreSQL + pgvector)
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/qwickcall
SYNC_DATABASE_URL=postgresql://user:password@host:5432/qwickcall
REDIS_URL=redis://default:password@host:port

# AI Engines
GROQ_API_KEY=gsk_...
GEMINI_API_KEY=AIzaSy...
DEEPGRAM_API_KEY=...
SARVAM_API_KEY=sk_...
STT_ENGINE=sarvam
TTS_ENGINE=sarvam
SARVAM_MODEL=bulbul:v3
SARVAM_SPEAKER=ritu

# Conversational Fillers Toggle (false = direct sub-400ms streaming)
ENABLE_CONVERSATIONAL_FILLERS=false

# RP Digital / Asterisk SIP (Optional for WebRTC calling)
RPDIGITAL_ASTERISK_HOST=kenyavoice.rpdigitalphone.com
RPDIGITAL_ASTERISK_PORT=5000
RPDIGITAL_ASTERISK_PATH=/ws
RPDIGITAL_SIP_USER=your_sip_extension
RPDIGITAL_SIP_PASSWORD=your_sip_password
RPDIGITAL_DID_NUMBER=+91XXXXXXXXXX

# Exotel Configuration (Optional for Indian PSTN Telephony)
EXOTEL_ACCOUNT_SID=your_exotel_account_sid
EXOTEL_API_KEY=your_exotel_api_key
EXOTEL_API_TOKEN=your_exotel_api_token
EXOTEL_CALLER_ID=+91XXXXXXXXXX
EXOTEL_APP_ID=your_voicebot_app_id
DEFAULT_ESCALATION_NUMBER=+919876543210
```

### 4. Database Initialization & Schema Bootstrapping

Initialize all tables, indexes, default SuperAdmin, demo Reseller, and AI agents:
```bash
python3 -c "from app.db.init_db import init_database; init_database()"
```

*(Optional)* Ingest the official 21-page PDF knowledge base for iDigitalStudies:
```bash
python3 scripts/ingest_official_pdf_knowledge.py
```

### 5. Launch the Server

Run via Uvicorn:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

---

## 🧪 Comprehensive Automated Test Suites

QwickCall includes 5 automated test suites covering all architectural layers:

```bash
# 1. Authentication, SSRF Sandbox, Pre-Call Wallet & TTS Cache Bounds
python3 tests/test_auth_and_security.py

# 2. Anti-Trolling, Graduated Deflection & Multi-Tenant Isolation
python3 tests/test_guardrails_and_tenants.py

# 3. Dual-Channel Voice & Chat RAG Execution
python3 tests/test_voice_and_chat.py

# 4. Clean Modular Architecture & Import Integrity Verification
python3 tests/test_structure.py

# 5. Latency & Sub-20ms In-Memory Semantic Cache Performance Validation
python3 tests/test_performance.py
```

---

## 📞 Outbound Calling via CLI

You can initiate an outbound AI voice call directly from the command line using `make_call.py`:

```bash
python3 make_call.py +919876543210
```

This validates credentials in `.env`, discovers the active public webhook URL, and issues an authenticated call request via Exotel to connect the recipient with the AI counselor.

---

## 🐳 Docker Deployment

Build and run the containerized application:

```bash
# Build the Docker image
docker build -t qwickcall:latest .

# Run the container
docker run -d \
  --name qwickcall \
  -p 8001:8001 \
  --env-file .env \
  qwickcall:latest
```

---

## 📄 License

Proprietary & Confidential — Built for QwickCall Enterprise Deployments.
