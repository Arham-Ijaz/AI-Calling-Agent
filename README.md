# 📞 AI Customer Calling Agent — Enterprise Voice & Telephony SaaS

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![MongoDB Atlas](https://img.shields.io/badge/MongoDB-Atlas%20Cloud-47A248.svg?style=flat&logo=mongodb&logoColor=white)](https://www.mongodb.com/atlas)
[![Google Gemini](https://img.shields.io/badge/Gemini%20AI-Flash%20Lite%202.0-4285F4.svg?style=flat&logo=google&logoColor=white)](https://aistudio.google.com/)
[![Twilio Telephony](https://img.shields.io/badge/Telephony-Twilio%20%2F%20SIP%20%2F%20PSTN-F22F46.svg?style=flat&logo=twilio&logoColor=white)](https://www.twilio.com/)
[![Edge-TTS](https://img.shields.io/badge/TTS-Edge--TTS%20Neural-0078D7.svg?style=flat&logo=microsoft&logoColor=white)](https://github.com/rany2/edge-tts)
[![WCAG AAA](https://img.shields.io/badge/Accessibility-WCAG%20AAA%20Compliant-success.svg?style=flat&logo=w3c&logoColor=white)](https://www.w3.org/WAI/WCAG2AAA-Conformance)
[![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat)](LICENSE)

An enterprise-grade, ultra-low-latency **AI Customer Calling Agent** designed for businesses, healthcare clinics, wholesale distributors, and customer support organizations. Built with a dual-pipeline architecture supporting both **Web Browser Live Voice Calls** (WebAudio API + WebSocket / SSE streaming) and **Direct Telephony Calls** (Twilio PSTN / SIP trunking with TwiML).

Powered by **FastAPI**, **Google Gemini 2.0 / Flash-Lite**, **Microsoft Edge-TTS Neural Streaming**, **MongoDB Atlas Cloud Database**, and an accessible **WCAG AAA compliant** responsive interface.

---

## 🌟 Key Architecture & Highlights

### 1. 🔒 Strict Authentication & Role-Based Access Control (RBAC)
* **Call Logs Route Guard (OWASP Top 10 A01):** Unauthenticated guest callers have strictly **zero access** to view, edit, or delete call logs. All call log endpoints (`GET /api/calls`, `GET /api/history`, `POST /api/calls/sync`, `DELETE /api/calls/{id}`) enforce HTTP 401 Unauthorized for guests.
* **Tenant Isolation:** Regular members see only their own call history, recordings, and orders. Platform Administrators can inspect all records or filter by user.
* **Guest Quota Wall & Full Admin Bypass:** Unauthenticated guest users are limited to a maximum of **3 interactions** (calls/messages) before triggering a mandatory Sign-In modal. Authenticated users and Administrators completely bypass all quota barriers with unlimited turns.
* **Prompt & FAQ Latency Optimization:** Stored FAQs are compressed into dense policy directives and dynamically chunked via semantic relevance scoring, reducing prompt token payload by **>85%** for ultra-fast TTFT.
* **Security & Injection Hardening:** Query builder rejects NoSQL injection vectors (operators, null-bytes, objects), while all dynamic DOM elements are escaped against Cross-Site Scripting (XSS).
* **Session Hardening:** HTTP-only secure JWT cookies (auto-detecting HTTPS schemes), timing-attack resistant password hashing, brute-force rate-limiting, and comprehensive security headers (`X-Content-Type-Options`, `X-Frame-Options`, `Permissions-Policy`, `Referrer-Policy`).

### 2. 🏢 Dynamic Branding & Whitelabeling (Centralized Business Profile)
* **Brand-Neutral Base:** Zero hardcoded brand names across prompts, UI elements, and templates.
* **Centralized Configuration:** Brand name, business industry, voice greetings, operational hours, human escalation replies, support phone, and email are tied directly to the **Business Profile & Compliance Settings** module.
* **Real-Time Dynamic Propagation:** Updating the business profile immediately updates the UI (navbar, document title, greetings, placeholders, typing indicators) and clears the backend voice prompt cache without requiring a server reboot.

### 3. 🎨 WCAG AAA Accessibility (a11y) & Vision-Friendly UI
* **High-Contrast Color Palette:** Complies with WCAG AAA contrast ratios (minimum 7:1 for normal text, 4.5:1 for large text) across both Light and Dark themes.
* **Non-Color Dependent Indicators:** Status indicators, badges, and alerts combine visual icons, distinct shapes, and explicit textual state labels (`role="status"`, `aria-live="polite"`).
* **Keyboard Navigable:** Prominent 3px focus rings (`:focus-visible`) across all interactive buttons, inputs, tabs, and modals.
* **Dual Theme Engine:** Instant toggling between Dark Mode and Light Mode with user preference persistence.

### 4. ⚡ Low-Latency Neural Voice Streaming (~0ms Audio Pipeline)
* **Concurrent Audio Queue:** Gemini streamed text tokens are decoupled from Edge-TTS audio generation so speech plays sentence-by-sentence with zero dead air.
* **Zero-Latency Fillers:** Pre-cached conversational fillers (*"Let me check that for you..."*, *"جی ایک سیکنڈ..."*) stream to the caller within milliseconds while background reasoning executes.
* **Bidirectional Audio:** Real-time **WebSocket** (`/ws/ai-call`) and **Server-Sent Events** (`/api/chat/stream`) with sentence-queue buffering.
* **Echo Elimination:** Hardware echo cancellation (`echoCancellation: true`) + microphone state gating (`IDLE -> LISTENING -> THINKING -> SPEAKING`) + recent-sentence overlap rejection.

### 5. ☁️ MongoDB Atlas & Serverless Caching
* Fully migrated to **MongoDB Atlas Cloud Database** (`pymongo`).
* **Vercel Connection Pool Caching:** The `MongoClient` is initialized globally outside route functions to reuse connection pools across serverless function invocations, preventing database exhaustion.
* **Atomic Sequence Generators:** Automatic integer IDs and audit trail logging for orders, handoffs, and call sessions.

---

## ⚡ Quick Start

### 🪟 Windows (One-Click)
1. Double-click **`run.bat`** (or execute `.\run.bat` in Command Prompt / PowerShell).
2. The launcher will automatically verify Python, set up your `.env`, install dependencies, and start the server at:
   **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

### 🍎 macOS / 🐧 Linux
```bash
chmod +x run.sh && ./run.sh
```

### 🛠️ Manual CLI Setup
```bash
# 1. Clone repository
git clone https://github.com/Arham-Ijaz/AI-Calling-Agent.git
cd AI-Calling-Agent

# 2. Configure environment variables
cp .env.example .env
# Edit .env and supply your MONGODB_URI and GEMINI_API_KEY

# 3. Install packages
pip install -r requirements.txt

# 4. Start production server
python app.py
```
Open **[http://127.0.0.1:8000](http://127.0.0.1:8000)** in Google Chrome or Microsoft Edge.

---

## ⚙️ Environment Variables Reference (`.env`)

| Variable | Required | Default | Description |
|---|:---:|---|---|
| `MONGODB_URI` | **Yes** | — | MongoDB Atlas cloud connection URI |
| `MONGODB_DB_NAME` | No | `ai_calling_db` | MongoDB database name |
| `GEMINI_API_KEY` | **Yes** | — | Primary Google Gemini API key ([Get Key](https://aistudio.google.com/app/apikey)) |
| `GEMINI_API_KEY_2` | No | — | Secondary API key for automatic quota failover |
| `GEMINI_MODEL` | No | `models/gemini-flash-lite-latest` | Active LLM model |
| `PORT` | No | `8000` | Application port |
| `GUEST_CALL_LIMIT` | No | `3` | Maximum free guest interactions before mandatory login |
| `JWT_SECRET` | No | Random | Secret key for signing JWT session cookies |
| `SESSION_COOKIE_NAME` | No | `calling_session` | Authentication cookie name |
| `DEFAULT_ADMIN_EMAIL` | No | `admin@callingagent.com` | Primary Super Administrator email |
| `DEFAULT_ADMIN_PASSWORD` | No | `AdminPass123!` | Initial administrator password |
| `TELEPHONY_PROVIDER` | No | `twilio` | Telephony provider (`twilio`, `telnyx`, `vonage`) |
| `TWILIO_ACCOUNT_SID` | Optional | — | Twilio Account SID for phone calls |
| `TWILIO_AUTH_TOKEN` | Optional | — | Twilio Auth Token |
| `TWILIO_PHONE_NUMBER` | Optional | — | E.164 phone number (e.g. `+12345678901`) |
| `TELEPHONY_WEBHOOK_URL`| Optional | — | Public URL for Twilio webhook callbacks |
| `DEFAULT_ENGLISH_VOICE` | No | `en-US-AriaNeural` | Edge-TTS neural voice for English |
| `DEFAULT_URDU_VOICE` | No | `ur-PK-UzmaNeural` | Edge-TTS neural voice for Urdu |

---

## 📡 API Endpoints Reference

### Authentication & Access Control
* `POST /api/auth/register` — Create new member account.
* `POST /api/auth/login` — Sign in and receive secure HTTP-only session cookie.
* `POST /api/auth/logout` — Sign out and invalidate session.
* `GET /api/auth/me` — Retrieve active user session profile (Protected).
* `PUT /api/auth/password` — Change password for authenticated user (Protected).

### Call Logs & Transcripts (Strictly Protected)
* `GET /api/calls` — Fetch recent call logs (Members view own; Admins view all; **Guests 401**).
* `GET /api/history?session_id={id}` — Full conversation transcript for a session (**Guests 401**).
* `POST /api/calls/sync` — Sync client-side session turns to database (**Guests 401**).
* `DELETE /api/calls/{call_id}` — Delete a call log and transcript records (**Guests 401**).

### Dynamic Branding & Business Settings
* `GET /api/business-settings` — Public endpoint returning active business profile and branding.
* `PUT /api/business-settings` — Update business profile, brand name, greetings, hours (**Admin only**).

### Voice & Chat Streaming
* `WebSocket /ws/ai-call` — Bidirectional real-time voice streaming with binary audio chunks.
* `POST /api/chat/stream` — Real-call Server-Sent Events (SSE) streaming sentence tokens.
* `POST /api/chat` — Fallback HTTP chat endpoint.
* `POST /api/tts` — Real-time text-to-speech audio synthesis.
* `GET /api/fillers` — Pre-cached conversational filler phrases.
* `POST /api/handoff` — Request human agent escalation ticket.

### Orders & Knowledge Base
* `GET /api/faqs` — Live FAQs referenced by AI agent.
* `POST /api/faqs` — Create new FAQ (**Admin only**).
* `GET /api/products` — Product and service catalog.
* `POST /api/orders` — Customer place order request (`pending_admin_confirmation`).
* `GET /api/orders/mine` — Retrieve authenticated user's orders.
* `GET /api/admin/orders` — Admin review and management of orders (**Admin only**).
* `POST /api/admin/orders/{id}/decision` — Approve or reject order (**Admin only**).

### Telephony (Twilio / SIP / PSTN)
* `GET/POST /api/telephony/voice` — Inbound phone call webhook (TwiML).
* `POST /api/telephony/gather` — Telephone speech recognition handler.
* `POST /api/telephony/status` — Telephone call status and duration tracker.
* `POST /api/telephony/outbound` — Place outbound phone call via REST API (**Admin only**).

---

## 🩺 System Health & Verification

The application provides a built-in health check and inspection endpoint:

```bash
# Verify system status, database connectivity & active Gemini API key:
curl http://127.0.0.1:8000/api/health
```

---

## ☁️ Vercel Serverless Deployment

1. Push your repository to GitHub.
2. Import the repository into [Vercel](https://vercel.com).
3. Set the Framework Preset to **Other** (FastAPI handled via `vercel.json`).
4. In **Project Settings -> Environment Variables**, add:
   - `MONGODB_URI`
   - `GEMINI_API_KEY`
   - `JWT_SECRET`
5. Click **Deploy**. Vercel will automatically deploy the FastAPI application with global MongoDB connection caching.

---

## 📄 License
This project is open-source software licensed under the [MIT License](LICENSE).
