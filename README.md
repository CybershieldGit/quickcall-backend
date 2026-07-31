# QwikCall — AI Voice Interview Practice Agent 🎙️

An AI-powered voice technical interview practice agent built with **FastAPI**, **Groq Llama 3.1 LLM**, **ElevenLabs / Edge-TTS**, and **Twilio Voice**.

Designed for **zero database overhead** and **₹0 operational cost**.

---

## 🌟 Features

- **Voice Interview Flow**: Greets candidates, asks for technical domain (Deep Learning, Python, React, DSA), and conducts a 5-question technical interview adaptively.
- **Zero Cost (₹0)**: Uses Groq API free tier, Edge-TTS free neural voices, and Twilio trial credits.
- **Zero Database Overhead**: Lightweight in-memory state tracking per call session.
- **Dual Mode**:
  1. **Twilio Voice Call**: Real phone call technical interview via Twilio numbers.
  2. **Web Call Simulator**: Polished in-browser phone simulator UI with live waveform visualizer & spoken voice output.
- **CLI Mode**: Terminal simulation script for quick testing without browser or phone audio.

---

## ⚡ Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment Variables (`.env`)

Create a `.env` file (or copy `.env.example`):

```bash
GROQ_API_KEY=gsk_...   # Get free key from https://console.groq.com
```

_(Optional: Set `ELEVENLABS_API_KEY` if you want ElevenLabs voice. If left blank, QwikCall automatically uses Microsoft Edge-TTS—100% free with zero API key required!)._

### 3. Run the Server

```bash
python -m uvicorn app.main:app --port 8001 --reload
```

---

## 🧪 Testing & Demos

### Option A: Web Call Simulator (Recommended for Interview Screen-Share Demo)

1. Open your browser at `http://localhost:8001`.
2. Click **"Start Voice Call"**.
3. Speak into your microphone (or type answers) to conduct a full live voice interview!

### Option B: Terminal CLI Mode

```bash
python cli_test.py
```

### Option C: Live Twilio Phone Call Integration

1. Run `ngrok http 8001` to expose your local server. Copy the HTTPS URL (e.g. `https://xxxx.ngrok-free.app`).
2. Go to [Twilio Console](https://console.twilio.com) -> Phone Numbers -> Active Numbers.
3. Set **A CALL COMES IN** Webhook URL to:
   `https://xxxx.ngrok-free.app/twilio/voice` (HTTP POST)
4. Dial your Twilio phone number from your mobile phone!

---

## 📁 Project Architecture

```text
QuickCall/
├── app/
│   ├── main.py                  # FastAPI entry point & routes
│   ├── config.py                # App settings & env loader
│   ├── core/
│   │   ├── interview_agent.py   # State machine & Groq LLM interview generator
│   │   └── tts_engine.py       # ElevenLabs + Edge-TTS fallback synthesis
│   └── routers/
│       ├── twilio_voice.py      # Twilio voice webhooks (TwiML Gather loop)
│       └── simulator_api.py     # Web Simulator REST API
├── static/
│   └── index.html               # Glassmorphic Web Call UI
├── cli_test.py                  # Terminal interview simulator
├── requirements.txt
└── README.md
```
