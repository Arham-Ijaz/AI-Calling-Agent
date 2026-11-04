# 📞 AI Customer Calling Agent — Production Voice & Telephony SaaS

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![Google Gemini](https://img.shields.io/badge/Gemini%20AI-Flash%20Lite%202.0-4285F4.svg?style=flat&logo=google&logoColor=white)](https://aistudio.google.com/)
[![Twilio Telephony](https://img.shields.io/badge/Telephony-Twilio%20%2F%20SIP%20%2F%20PSTN-F22F46.svg?style=flat&logo=twilio&logoColor=white)](https://www.twilio.com/)
[![Edge-TTS](https://img.shields.io/badge/TTS-Edge--TTS%20Neural-0078D7.svg?style=flat&logo=microsoft&logoColor=white)](https://github.com/rany2/edge-tts)
[![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat)](LICENSE)

An enterprise-grade, ultra-low-latency **AI Customer Calling Agent** built for businesses, clinics, and wholesale retail networks. Designed with a dual-pipeline architecture supporting both **Web Browser Voice Calls** (WebAudio API + WebSocket / SSE streaming) and **Direct Telephone Calls** (Twilio PSTN / SIP trunking with TwiML).

Powered by **FastAPI**, **Google Gemini 2.0 / Flash-Lite**, **Microsoft Edge-TTS Neural Streaming**, and an embedded relational SQLite database with strict Role-Based Access Control (RBAC).

---

## ⚡ Quick Start (One-Click Launchers)

The application includes zero-configuration automatic launchers that verify Python, set up your `.env`, install dependencies from `requirements.txt`, and boot the server.

### 🪟 Windows (Double-Click Launch)
1. Double-click **`run.bat`** (or execute `.\run.bat` in Command Prompt / PowerShell).
2. The launcher will automatically:
   - Verify Python 3.10+
   - Create `.env` from `.env.example` if not already present
   - Install dependencies from `requirements.txt`
   - Prompt for your free Gemini API key if unconfigured
   - Start the server at: **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

### 🍎 macOS / 🐧 Linux (Terminal Launch)
```bash
chmod +x run.sh && ./run.sh
```

### 🛠️ Manual CLI Launch
```bash
# 1. Clone repository
git clone https://github.com/Arham-Ijaz/AI-Calling-Agent.git
cd AI-Calling-Agent

# 2. Configure environment
cp .env.example .env
# Edit .env and supply GEMINI_API_KEY (Get free key: https://aistudio.google.com/app/apikey)

# 3. Install packages
pip install -r requirements.txt

# 4. Start production server
python app.py
```
Open **[http://127.0.0.1:8000](http://127.0.0.1:8000)** in Google Chrome or Microsoft Edge.

---

## 🌟 Key Architecture & Capabilities

### 🎙️ 1. Real-Call Low Latency Voice Streaming (~0ms Audio Pipeline)
* **Concurrent Audio Queue:** Streaming text tokens from Gemini are decoupled from Edge-TTS audio generation so speech plays sentence-by-sentence with zero dead air.
* **Immediate Zero-Latency Fillers:** Pre-cached conversational fillers (*"Let me check that for you..."*, *"جی ایک سیکنڈ..."*) stream to the audio output within milliseconds while background reasoning executes.
* **Dual Transport:** Supports both bidirectional **WebSocket** (`/ws/ai-call`) and **Server-Sent Events** (`/api/chat/stream`) with sentence-queue audio buffering.

### 📞 2. Telephony API Integration (PSTN & SIP Numbers)
* **Twilio Inbound Voice Webhook** (`/api/telephony/voice`): Connect any real phone number to your AI calling agent via TwiML XML.
* **Interactive Speech Gather** (`/api/telephony/gather`): Real-time speech recognition across telephone networks, executing the same instant fast-path and escalation logic.
* **Outbound Calling API** (`POST /api/telephony/outbound`): Admin-triggered automated outbound voice calls via Twilio REST API.
* **Call Status Callbacks** (`/api/telephony/status`): Automatically logs telephone call duration, status, and transcripts in SQLite.

### 🛡️ 3. Self-Listening & Echo Elimination
* **Mic Gating State Machine:** `IDLE -> LISTENING -> THINKING -> SPEAKING -> LISTENING`. The microphone hardware stream is actively aborted (`recognition.abort()`) and gated during playback.
* **Hardware Echo Cancellation:** Requests `{ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } }` on browser mic streams.
* **Token Overlap Safety Net:** Normalizes the last 3 agent sentences and rejects any mic input that overlaps $\ge 60\%$ with recent bot speech or known filler phrases within a 3-second window.

### 🏢 4. Multi-Tenant Knowledge Base & Admin Confirmation Flow
* **Generic Business Settings:** Editable business name, working hours, greetings, and escalation rules via the Admin panel.
* **Admin Confirmation Mandatory:** When callers request an order or appointment, the backend marks it `pending_admin_confirmation`. The agent never makes unauthorized guarantees.
* **Admin Order Badges:** Navbar dynamically indicates pending confirmation count (`X Pending`), cancellation requests (`X Cancel`), and active orders (`X In Process`).
* **Guest Limit Guard:** Guests without sign-up receive a configurable 5-turn free trial before gracefully prompting for account registration.

### 🔄 5. Dual Gemini API Failover
* Supports primary (`GEMINI_API_KEY`) and secondary (`GEMINI_API_KEY_2`) keys.
* Automatically fails over on quota exhaustion (`429 ResourceExhausted`) without dropping calls.

---

## ⚙️ Environment Variables Reference (`.env`)

| Variable | Required | Default | Description |
|---|:---:|---|---|
| `GEMINI_API_KEY` | **Yes** | — | Primary Google Gemini API key |
| `GEMINI_API_KEY_2` | No | — | Secondary API key for automatic quota failover |
| `GEMINI_MODEL` | No | `models/gemini-flash-lite-latest` | Active LLM model |
| `PORT` | No | `8000` | Local web server port |
| `TELEPHONY_PROVIDER` | No | `twilio` | Telephony provider (`twilio`, `telnyx`, `vonage`) |
| `TWILIO_ACCOUNT_SID` | Optional | — | Twilio Account SID for phone call integration |
| `TWILIO_AUTH_TOKEN` | Optional | — | Twilio Auth Token |
| `TWILIO_PHONE_NUMBER` | Optional | — | E.164 phone number (e.g. `+12345678901`) |
| `TELEPHONY_WEBHOOK_URL`| Optional | — | Public webhook URL (e.g. ngrok or Cloudflare tunnel) |
| `TELEPHONY_DEFAULT_VOICE`| No | `Polly.Aditi` | Voice used for telephone calls |
| `TELEPHONY_LANGUAGE` | No | `en-US` | Telephony speech recognition language |
| `JWT_SECRET` | No | `...` | Secret key for JWT session tokens |
| `DEFAULT_ADMIN_EMAIL` | No | `admin@example.com` | Default admin email |
| `DEFAULT_ADMIN_PASSWORD`| No | `AdminPass123!` | Default admin password |
| `GUEST_CALL_LIMIT` | No | `5` | Free caller turns before requiring sign-up |

---

## 📡 API Endpoints Reference

### Voice & Chat Streaming
* `WebSocket /ws/ai-call` — Bidirectional real-time voice streaming with Edge-TTS binary audio chunks.
* `POST /api/chat/stream` — Real-call Server-Sent Events (SSE) streaming sentence tokens.
* `POST /api/chat` — Fallback HTTP dialogue turn endpoint.
* `POST /api/tts` — Real-time text-to-speech audio synthesis.
* `GET /api/fillers` — Pre-cached conversational filler phrases.

### Telephony (Twilio / SIP / PSTN)
* `GET/POST /api/telephony/voice` — Inbound telephone call entrypoint (returns TwiML).
* `POST /api/telephony/gather` — Telephone speech recognition handler.
* `POST /api/telephony/status` — Telephone call status and duration tracker.
* `POST /api/telephony/outbound` — Admin endpoint to place outbound phone calls.
* `GET /api/telephony/config` — Telephony connection diagnostic status.

### Orders & Knowledge Base
* `GET /api/faqs` — Suggested FAQ questions for callers.
* `GET /api/faqs/all` — Internal FAQ management (Admin only).
* `GET /api/products` — Product and service catalog.
* `GET /api/admin/orders/summary` — Order status summary counts (`pending`, `cancel_requested`, `confirmed`).
* `POST /api/admin/orders/{id}/decision` — Admin order decision (`confirm`, `reject`, `cancel_approve`, `completed`).

---

## 🧪 Testing & Verification

Run the automated test suites:

```bash
# 1. Run full 45-case benchmark evaluation suite
python scripts/evaluate.py

# 2. Run pytest pipeline unit tests (SSE, Fast Path, WebSocket)
python -m pytest tests/

# 3. Run client echo-cancellation unit tests
node tests/test_echo_filter.js
```

---

## 📄 License
This project is open-source software licensed under the [MIT License](LICENSE).
