# QwickCall — Frontend Architecture & Executive B2B Onboarding Guide

> **Confidential — Enterprise Voice AI Platform Blueprint**  
> **Aesthetic Standard**: Ultra-Minimal, Polished, Executive (Linear / Apple macOS / Vercel style)  
> **Business Model**: 3-Tier Multi-Tenant Wholesale & Reseller Distribution Engine  

---

## 1. Executive Summary & Design Philosophy

QwickCall is an enterprise-grade conversational AI platform built for the Indian and global enterprise market. It bridges traditional and cloud telephony (**Exotel, Asterisk SIP, FreePBX, Tata Tele, Airtel**) with real-time streaming AI pipelines (**Deepgram STT, Groq/Gemini LLM, and Sarvam/EdgeTTS**).

### Design Philosophy
* **Zero Clutter**: Avoid loud gradients, neon glows, or consumer gaming aesthetics.
* **Refined Palette**: Deep obsidian and graphite canvas (`#090A0F`, `#12151C`), razor-thin hairline borders (`rgba(255, 255, 255, 0.07)`), and subtle surface elevations.
* **Typography**: Crisp, high-legibility modern sans-serif typography (`Inter` / Apple SF Pro system stack) with clean optical hierarchy.
* **Micro-Interactions**: Subtle glassmorphism (`backdrop-filter: blur(20px)`), smooth tab transitions, and restrained status indicators (emerald for active/healthy, subtle amber for pending, slate for inactive).
* **Operating Standard**: 100% white-glove, high-touch executive onboarding. Clients and resellers do not navigate a confusing self-serve labyrinth; your company executives onboard them smoothly in minutes.

---

## 2. Multi-Role Portal Architecture (4 Views)

The frontend application (`static/index.html`) operates with clean role-based switching:

```
┌────────────────────────────────────────────────────────────────────────┐
│                         QwickCall App Switcher                         │
├───────────────┬──────────────────────┬─────────────────┬───────────────┤
│ 1. SuperAdmin │ 2. Reseller Partner  │ 3. Client Space │ 4. Showcase   │
│   (Platform)  │    (Whitelabel)      │ (Day-to-Day AI) │  (Live Pitch) │
└───────────────┴──────────────────────┴─────────────────┴───────────────┘
```

---

### View 1: Platform SuperAdmin Portal (`#view-admin`)
Used exclusively by your company leadership and platform operations team.

* **Executive Financial Telemetry**:
  * **Platform Gross Revenue (₹)**: Total airtime billed to all direct clients and reseller partners.
  * **Underlying Telco & AI Cost (₹)**: Actual infrastructure spend (Deepgram + Groq + Sarvam + SIP/Exotel).
  * **Net Platform Profit Margin (₹)**: Instant visibility into platform net gross profit.
  * **Call Volume**: Total minutes processed, concurrent active calls, and overall system latency.
* **Reseller Partner Management**:
  * View and provision white-label agencies/resellers.
  * Set wholesale rate per minute (e.g., default `₹3.00/min`).
  * Monitor partner prepaid wallets and total sub-clients onboarded.
  * One-click wallet top-up modal for offline bank transfer reconciliation.
* **Direct Enterprise Accounts**:
  * Manage high-value direct accounts (non-reseller clients).
  * Set custom rate-per-minute overrides, monitor usage, and adjust credit balances.
* **Master Telephony DID Pool**:
  * Inventory table of virtual numbers (+91 Indian DIDs).
  * Status badges: `Active`, `Available`, `Reserved`.
  * Instantly assign or reclaim DIDs across tenants with zero carrier wait time.

---

### View 2: Reseller Partner Portal (`#view-reseller`)
Given to marketing agencies, BPO providers, and software resellers who resell your voice platform under their own brand.

* **Whitelabel Customization**:
  * Reseller Brand Name, Custom Logo URL, Support Contact Email, and Subdomain.
  * Ensures their enterprise clients see only the partner's branding.
* **Live Margin & Financial Dashboard**:
  * **Wholesale Rate (Buy)**: e.g., `₹3.00/min` (set by your SuperAdmin).
  * **Average Retail Rate (Sell)**: e.g., `₹5.00/min` (set by Reseller).
  * **Net Reseller Profit Margin**: Visual badge highlighting `✨ Margin: ₹2.00/min`.
  * **Reseller Wallet Balance**: Real-time balance deducting only at wholesale cost.
* **Sub-Client Portfolio**:
  * Table of onboarded business clients.
  * Per-client retail rate control (e.g., charge Client A `₹5.00/min` and Client B `₹6.50/min`).
  * Direct access to launch new sub-clients via the executive onboarding wizard.

---

### View 3: Client Business Workspace (`#view-client`)
The day-to-day workspace for the end business running AI calls.

* **Telephony & Connection Hub**:
  * Displays assigned DID number and current telephony routing mode.
  * Direct configuration for BYOC Exotel, SIP Trunk, or PSTN Forwarding.
  * **Human Escalation Number**: Dedicated phone number or SIP extension where calls are transferred when a caller asks for a human agent.
* **Dual-PDF Knowledge Base & RAG**:
  * **Slot 1 (Company Overview)**: Mission, business policies, FAQ, terms, hours of operation.
  * **Slot 2 (Product & Service Catalog)**: Pricing tiers, SKU details, service packages, qualifying questions.
  * **Website & Sitemap URL Ingestion**: One-click text crawler to sync knowledge directly from the client's live website.
* **AI Persona & Voice Studio**:
  * System prompt editor with dynamic variable tags (`{{customer_name}}`, `{{due_date}}`).
  * Bilingual Language selection: English, Hindi, Hinglish.
  * Voice model picker: Sarvam AI Indian accents (`Bulbul`), EdgeTTS, or Deepgram Aura.
* **Live In-Call Console**:
  * Real-time audio waveform visualizer (canvas-based 60fps audio oscillation).
  * Live streaming transcript display showing bidirectional dialogue (User ↔ AI).
  * Active call state pill: `Ringing`, `In-Call (00:42)`, `Transferred`, `Completed`.
* **Leads CRM & Call Analytics**:
  * Searchable and filterable call history.
  * Call metrics: Duration, billable airtime, sentiment score (`Positive`, `Neutral`, `Needs Escalation`).
  * Qualification disposition: `Lead Qualified`, `Callback Requested`, `Not Interested`, `Voicemail`.
  * Audio recording player and full conversational transcript export.

---

### View 4: Live Pitch / Executive Showcase (`#view-showcase`)
A dedicated interactive demonstration console used by your company executives during sales calls.
* Allows prospective clients to test voice responsiveness and interruption/barge-in latency directly inside the browser or via a live dial-in test.

---

## 3. Executive High-Touch Onboarding Workflow

Because this is a high-value B2B enterprise platform, onboarding is **never an unguided self-service form**. It is conducted by your company executive over a 15-minute onboarding call using the built-in **4-Step Onboarding Wizard Modal (`#wizard-modal`)**.

```
[ Step 1: Profile & Ownership ] 
           │
           ▼
[ Step 2: Telephony Integration (Turnkey vs Existing IVR) ] 
           │
           ▼
[ Step 3: AI Persona & Regional Voice Tuning ] 
           │
           ▼
[ Step 4: Knowledge Ingestion (PDF/URL) & Commercials ]
```

### Step 1: Company Profile & Commercial Attribution
* **Tenant / Company Name**: e.g., `Apex Logistics Pvt Ltd`.
* **Primary Executive Contact**: Name, official business email, and WhatsApp number.
* **Account Type / Attribution**: Direct Platform Client OR assign to a specific Reseller Partner.
* **Initial Business Goal**: Inbound Support, Outbound Lead Qualification, or Appointment Booking.

### Step 2: Telephony Selection Matrix
The executive asks: *"Do you need a new number, or do you have an existing telephony system?"*

* **Option A — Master Pool DID (Turnkey, Instant)**:
  * The executive selects an available virtual number from your master pool (+91).
  * Zero wait time. Active immediately.
* **Option B — Bring Your Own Exotel (BYOC)**:
  * Used if the client already has an Exotel account.
  * Input fields: `Account SID`, `API Key`, `API Token`, `Custom App ID`.
  * Displays the tenant-specific WebSocket streaming URL:
    `wss://api.qwickcall.com/ws/exotel?tenant_id=ten_xxxx`
* **Option C — SIP Trunk / PBX Extension**:
  * Used for Asterisk, FreePBX, Tata Tele, Cisco, or legacy PBX.
  * Input fields: `SIP Host/IP`, `SIP Port`, `Extension/Auth Name`.
* **Human Escalation Setting**:
  * Input: `Human Transfer Number` (e.g. `+918045678900` or SIP Ext `101`).
  * If caller asks for a supervisor, the AI executes a warm transfer to this number.

### Step 3: AI Voice & Persona Tuning
* **Agent Name & Role**: e.g., *"Priya — Senior Customer Relationship Officer"*.
* **Primary Language**: Select English or Hindi / Hinglish.
* **Voice Provider**: Select Sarvam AI (Indian accents), Deepgram, or EdgeTTS.
* **Base Prompt Template**: Select from industry presets (E-commerce Order Status, Healthcare Appointment, B2B Lead Qualification, Debt Recovery).

### Step 4: Knowledge Base & Commercial Settlement
* **Knowledge Ingestion**:
  * Upload Document 1: Company Overview / FAQs (PDF).
  * Upload Document 2: Product & Service Pricing Catalog (PDF).
  * (Optional) Enter Client Website URL for automated page scraping.
* **Commercial Terms**:
  * **Retail Rate per Minute**: e.g., `₹5.00/min`.
  * **Initial Prepaid Wallet Credit**: e.g., `₹5,000` (provisioned immediately upon bank transfer / invoice payment).
* **Launch**: Click **"Activate Tenant"**. The AI voice agent is live and testable within 30 seconds.

---

## 4. How Existing IVRs Connect (Architectural Matrix)

When prospective clients say: *"We already have an IVR (Exotel, Asterisk, Tata Tele, Airtel PRI, Twilio), can we keep our numbers?"*, the answer is **YES**.

| Existing Setup | Technical Integration Method | Executive Steps on Onboarding Call |
| :--- | :--- | :--- |
| **Existing Exotel Account** | Exotel **Voicebot Applet** in App Bazaar | 1. Enter Exotel SID/Key/Token in Step 2.<br>2. Open client's Exotel App Bazaar.<br>3. Drag *Voicebot Applet* onto the flow (or on Option 2 / After-Hours).<br>4. Paste `wss://api.qwickcall.com/ws/exotel?tenant_id=...`. |
| **Existing Asterisk / FreePBX** | **SIP URI Extension** bridge | In client's `extensions.conf`:<br>`exten => 800,1,Dial(PJSIP/ai@147.79.71.199:5060)`<br>Callers pressing Option 2 are routed directly to QwickCall. |
| **Legacy Telco / PRI / Airtel / Tata** | **Conditional Call Forwarding** | Client telecom admin enables standard call forwarding on their hunt group:<br>• Forward on Busy<br>• Forward on No-Answer (after 3 rings)<br>• Forward After-Hours (7 PM – 9 AM)<br>Points to a dedicated QwickCall DID. |
| **Warm Transfer Back to Human** | **SIP REFER / PSTN Bridging** | When the AI detects user intent for human support, it executes the `transfer_call` tool, bridging the caller back to the client's internal call center queue. |

---

## 5. Frontend Technical Directory & Component Structure

```
QwickCall/
├── static/
│   ├── index.html              # Core single-page interface with 4 views & onboarding wizard
│   ├── css/
│   │   └── style.css           # Modern graphite theme, CSS tokens, glassmorphism, responsive grid
│   └── js/
│       ├── app.js              # State manager, view switcher, auth/role handlers
│       ├── admin_controller.js # SuperAdmin telemetry, reseller tables, DID pool logic
│       ├── reseller_portal.js  # Reseller margin calculations, sub-client CRUD, branding
│       ├── client_workspace.js # RAG PDF uploader, prompt editor, live call monitor, leads CRM
│       ├── wizard.js           # 4-step executive onboarding modal controller
│       └── audio_visualizer.js # Canvas-based real-time 60fps waveform for live calls
```

### Design System Tokens (CSS Variables)

```css
:root {
  /* Surfaces */
  --bg-primary: #090A0F;           /* Deep obsidian canvas */
  --bg-surface: #12151C;           /* Card & panel surface */
  --bg-surface-hover: #181C26;     /* Interactive hover state */
  --bg-modal: #0E1118;             /* Dialog & modal background */

  /* Hairline Borders */
  --border-subtle: rgba(255, 255, 255, 0.07);
  --border-focus: rgba(255, 255, 255, 0.18);

  /* Typography */
  --font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif;
  --text-primary: #F3F4F6;
  --text-secondary: #9CA3AF;
  --text-muted: #6B7280;

  /* Executive Accents */
  --accent-emerald: #10B981;       /* Operational health, positive balance, margin */
  --accent-amber: #F59E0B;         /* Warnings, low balance */
  --accent-primary: #6366F1;       /* Subtle indigo branding element */
}
```

---

## 6. Real-Time Telemetry & Billing Ledger

Whenever an inbound or outbound call terminates:
1. `call_logger.py` calculates billable duration (rounded to nearest 60s standard Indian telecom pulse).
2. For Reseller sub-clients:
   * Client wallet is debited at Retail Rate (e.g., `₹5.00/min`).
   * Reseller wholesale cost is debited from Reseller wallet at Wholesale Rate (e.g., `₹3.00/min`).
   * Reseller Net Profit (e.g., `₹2.00/min`) is credited to Reseller's earnings ledger.
   * Platform Net Profit is recorded in `BillingTransaction` table.
3. The frontend displays updated real-time balances immediately without full page reloads.

---

## Summary Checklist for Company Executives

When onboarding a new enterprise client:
- [ ] Open QwickCall Portal ➔ Click **"New Onboarding"**.
- [ ] Fill Step 1 (Company Details & Reseller Attribution).
- [ ] Check client's telephony requirements in Step 2:
  - *No existing number?* ➔ Allocate from Master Pool DID.
  - *Has Exotel?* ➔ Select BYOC Exotel, paste credentials, add WebSocket URL to their App Bazaar.
  - *Has Asterisk/PBX?* ➔ Provide SIP URI or set PSTN Call Forwarding.
  - *Set Human Escalation Number* for warm transfers.
- [ ] Configure Voice & Tone in Step 3 (Select Sarvam Indian voice, language, prompt).
- [ ] Upload Knowledge PDFs / enter Website URL in Step 4.
- [ ] Set Billing Rate (₹/min) & Top-up Initial Wallet Credit.
- [ ] Perform live dial-in test call to verify latency and AI response accuracy.
- [ ] Issue client credentials with access to their clean, minimal Client Workspace.
