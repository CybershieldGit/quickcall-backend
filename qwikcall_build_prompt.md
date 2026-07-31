# QwikCall — AI Interview Practice Platform (Build Spec)

You are building a production-grade backend service called **QwikCall**: an AI-powered voice interview practice platform that candidates interact with over a real phone call.

## Goal

A candidate dials a phone number (Twilio). The system picks up, conducts a multi-turn technical interview by voice — asking questions, listening to spoken answers, adaptively generating follow-up questions based on answer quality, and scoring the candidate — then ends the call and produces a full transcript + evaluation report. Everything must run on free-tier services only (no paid infrastructure).

## Tech stack (all free-tier)

- **Backend framework:** FastAPI (Python), async, WebSocket support
- **Telephony:** Twilio Voice + Twilio Media Streams (trial account, inbound calls only — no verified-caller-ID restriction applies to inbound)
- **Speech-to-text:** Deepgram streaming API (trial credit), configured for µ-law 8kHz audio (Twilio's native call audio format)
- **LLM (question generation + evaluation):** Groq API (free tier, Llama 3.x models) as primary; structure the LLM client behind an interface so it can be swapped for Google Gemini free tier or OpenAI GPT with a one-line config change
- **Text-to-speech:** ElevenLabs free tier, output converted to µ-law 8kHz for Twilio playback
- **Database:** MongoDB Atlas free tier (M0 cluster)
- **Deployment target for demo:** a single public HTTPS URL that Twilio's webhook can reach

## Core functional requirements

### 1. Call orchestration

- Twilio webhook (`POST /twilio/voice`) receives the incoming call, returns TwiML that starts a bidirectional Media Stream to a WebSocket endpoint (`/twilio/stream`)
- WebSocket endpoint handles the Twilio Media Streams protocol: receives base64-encoded µ-law audio chunks (`media` events), buffers/streams them to Deepgram, and sends synthesized audio back as outbound `media` events
- Handle Twilio's `start`, `media`, `stop` event types correctly per their Media Streams protocol spec
- Greet the candidate, ask which domain they want to interview for (or read it from call metadata / a pre-registered session), then begin the interview loop

### 2. Real-time speech pipeline

- Stream incoming call audio to Deepgram's streaming STT in real time (not batch) — this is required to hit low end-to-end latency
- On each finalized transcript segment from Deepgram, pass it to the conversation orchestrator
- Stream the LLM's response text to TTS as it's generated where possible (sentence-level chunking) rather than waiting for the full response, to minimize perceived latency
- Target: keep round-trip latency (candidate finishes speaking → agent starts responding) as low as practically achievable; log latency per turn so it can be measured and reported

### 3. Conversation memory & adaptive questioning

- Maintain full conversation history per call session (in-memory during the call, persisted to MongoDB after)
- System prompt / orchestration logic must:
  - Ask domain-appropriate technical questions (support at least 3 domains, e.g. "Backend/Python", "Frontend/React", "Data Structures & Algorithms" — make domains configurable via a config file, not hardcoded)
  - Evaluate each answer for correctness/depth
  - Generate adaptive follow-up questions: if an answer is shallow, ask a deeper follow-up on the same topic; if strong, move to a new topic
  - Track how many questions have been asked and end the interview after a configurable number (default 5–7 exchanges) or after a configurable time limit
  - Close the call with a brief spoken summary/sign-off

### 4. Scoring & transcript generation

- After each answer, generate a structured evaluation: correctness score (1–5), a short rationale, and tags for topics covered
- At call end, generate:
  - Full transcript (speaker-labeled, timestamped)
  - Per-question scores
  - An overall candidate score and a short written summary of strengths/weaknesses
- Persist all of this to MongoDB under the session document

### 5. Session management (MongoDB)

- Collections: `sessions` (one doc per interview call — candidate info if available, domain, start/end time, status), `transcripts` (or embedded in session doc), `scores`
- Each session must be resumable/inspectable via a simple internal API even after the call ends (for building a dashboard later — not required now, but design the schema so it's possible)

### 6. REST API (in addition to the Twilio webhook/WebSocket)

- `POST /sessions` — create a new interview session (domain, optional candidate name) and return session ID
- `GET /sessions/{id}` — fetch session status, transcript, scores
- `GET /sessions/{id}/report` — return a formatted evaluation report
- `GET /health` — health check

## Non-functional requirements

- All API keys/secrets (Twilio, Deepgram, Groq/Gemini, ElevenLabs, MongoDB URI) loaded from environment variables, never hardcoded — include a `.env.example`
- Log every pipeline stage's latency (STT time, LLM time, TTS time, total turn time) so real numbers can be reported, not estimated
- Graceful handling of: candidate silence/no response, STT low-confidence transcripts, LLM API failures (retry/fallback), call disconnects mid-interview (save partial session)
- Include a `README.md` explaining: how to get free API keys for each service, how to set up the Twilio phone number and webhook URL, how to run locally with ngrok, and how to deploy to Render/Railway free tier
- Include a lightweight local test mode: a script or endpoint that lets you simulate a full interview via text input (bypassing Twilio/audio) for fast iteration without burning call minutes

## Deliverables

1. Full FastAPI project structure (routers, services, models, config, `requirements.txt`)
2. Twilio Media Streams WebSocket handler
3. Deepgram streaming integration
4. LLM orchestration module (interview logic, adaptive follow-ups, scoring) with a swappable provider interface
5. ElevenLabs TTS integration with µ-law conversion for Twilio playback
6. MongoDB models/schema and connection setup
7. `README.md` with full setup instructions for every free-tier service used
8. `.env.example`

## Build order (please build in this sequence, confirming each stage works before moving on)

1. FastAPI skeleton + MongoDB connection + `/health` endpoint
2. Text-only interview loop (LLM orchestration, adaptive questioning, scoring) testable via a REST/CLI interface — no audio yet
3. Add ElevenLabs TTS + Deepgram STT as standalone modules, tested independently (e.g. file in → transcript out; text in → audio file out)
4. Add Twilio webhook + Media Streams WebSocket, wire the pipeline together for a live call
5. Add persistence of transcripts/scores to MongoDB, then the report endpoints
6. Latency logging and cleanup pass

Start by proposing the project folder structure, then begin at step 1.
