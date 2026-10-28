# 📞 DigiBuzz — AI Customer Calling Agent

An intelligent, voice-enabled AI Customer Calling Agent tailored for wholesale B2B retail networks. Powered by **FastAPI**, **Google Gemini AI**, **gTTS**, and a modular multi-page responsive web dashboard.

Customers and retailers can speak or chat naturally with the AI to inquire about product prices, stock status, delivery policies, credit ledger tracking (Digital Khata), and human manager escalation.

---

## ⚡ Quick Start (Pull & Play)

You can run the entire system on **any computer** with zero manual setup. The launcher automatically detects Python, creates your `.env` configuration, installs all required packages from `requirements.txt`, and boots the server.

---

### 🚀 Method 1: The Easiest 1-Command Auto-Start (Recommended)

#### 🪟 On Windows:
1. Open this folder in Windows File Explorer.
2. Simply **double-click** on **`run.bat`** (or open Command Prompt / PowerShell and type `run.bat`).
3. That's it!
   - It will automatically verify Python.
   - It will download and install all libraries from `requirements.txt`.
   - If you haven't added your Gemini API key yet, it will ask you to paste it in the console and automatically save it to `.env`.
   - The server will start and be live at: **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

#### 🍎 On macOS / 🐧 On Linux:
1. Open terminal in the project directory.
2. Run:
   ```bash
   chmod +x run.sh && ./run.sh
   ```
3. It installs all dependencies, configures `.env`, and starts the server on **[http://127.0.0.1:8000](http://127.0.0.1:8000)**.

---

### 🛠️ Method 2: Manual Step-by-Step CLI Start

If you prefer running commands manually:

```bash
# 1. Clone the repository
git clone https://github.com/Arham-Ijaz/-AI-Calling-Agent.git
cd ai-calling-agent

# 2. Configure .env file
# Windows:
copy .env.example .env
# Mac/Linux:
cp .env.example .env

# Open .env and paste your GEMINI_API_KEY
# (Get a free key from: https://aistudio.google.com/app/apikey)

# 3. Install dependencies
pip install -r requirements.txt

# 4. Start the application
python app.py
```

Once running, open your web browser to:
👉 **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

---

## 🌟 Key Highlights & Features

### 🎙️ 1. Voice Calling & Audio Coordination
- **Native Browser STT**: Web Speech API speech-to-text with zero cloud latency.
- **Auto Microphone Arming**: After the AI finishes speaking, the microphone activates automatically so the user can speak without pressing buttons.
- **Half-Duplex Echo Prevention**: The microphone automatically locks while the AI speaks, preventing speaker output from feeding back into the voice recognizer.
- **Voice-Triggered Call Termination**: Saying *"End call"*, *"Finish call"*, or *"Bye"* automatically ends the call with a polite goodbye.

### ⚡ 2. Immediate Filler Speech (Zero Dead Air)
- When a user speaks, the server instantly plays a natural audio filler (*"Let me check that for you..."*, *"One moment please..."*) while Gemini processes the query.
- Eliminates awkward silence and provides a realistic phone call experience.

### 💬 3. Interactive FAQ Slide Bar & Carousel
- Smooth horizontal carousel displaying suggested FAQ questions.
- Includes **left / right arrow buttons**, **mouse drag-to-scroll**, and **mouse wheel horizontal scrolling**.
- Clicking any suggested chip automatically starts a call or sends the query.

### 🔄 4. Dual Gemini API Failover
- Configure both `GEMINI_API_KEY` (primary) and `GEMINI_API_KEY_2` (secondary).
- If the primary key exhausts its free rate limit (`HTTP 429 ResourceExhausted`), the backend automatically and seamlessly switches to the backup key with zero interruption.
- Navbar badge shows real-time key status:
  - 🟢 **Primary Key Active**
  - 🟡 **Switched to Secondary Key**
  - 🔴 **Both Keys Exhausted**

### 📦 5. Real-Time Dynamic Knowledge Base & Gapless Resequencing
- Full CRUD for FAQs and Product Catalog via clean UI tables.
- **Immediate In-Memory Refresh**: New products or FAQs added in the UI are immediately available to the AI agent during live calls without restarting the server.
- **Gapless Auto-Resequencing**: Deleting any product or FAQ automatically resequences table IDs from `1` to `N` with zero number gaps (no jumping from 20 to 22 or 24).

### 🌓 6. Dark & Light Dual Theme
- Fully styled dark and light themes designed for comfortable all-day use.
- Theme preference is stored in `localStorage` and persists across reloads.

### 📋 7. Call Logs & Session Transcripts
- Embedded SQLite relational database tracks call duration, message count, detected intents, and resolution outcome (`Resolved` vs `Escalated`).
- Inspect complete chronological conversation transcripts for every session.

---

## 🛠️ Requirements & Tech Stack

| Component | Technology | Description |
|-----------|------------|-------------|
| **Backend** | [FastAPI](https://fastapi.tiangolo.com/) | High-performance Python async REST API |
| **Server** | [Uvicorn](https://www.uvicorn.org/) | Lightning-fast ASGI web server |
| **LLM Engine** | [Google Gemini](https://ai.google.dev/) | `models/gemini-flash-lite-latest` for low-latency dialogue |
| **TTS Engine** | [gTTS](https://pypi.org/project/gTTS/) | Google Text-to-Speech with memory caching |
| **STT Engine** | Web Speech API | Client-side voice recognition |
| **Database** | SQLite3 | Embedded zero-config database with WAL journaling |
| **Frontend** | Vanilla JS / CSS3 / HTML5 | Responsive multi-page SPA without external framework bloat |

---

## ⚙️ Configuration (`.env`)

| Variable | Required | Default | Description |
|----------|:--------:|---------|-------------|
| `GEMINI_API_KEY` | **Yes** | — | Primary Google Gemini API Key |
| `GEMINI_API_KEY_2` | No | — | Secondary API Key for automatic quota failover |
| `GEMINI_MODEL` | No | `models/gemini-flash-lite-latest` | Gemini model name |
| `PORT` | No | `8000` | Port for the local web server |

---

## 📂 Project Directory Structure

```text
├── app.py                  # FastAPI server & AI logic
├── index.html              # Multi-page responsive SPA shell
├── run.bat                 # One-click Windows auto launcher
├── run.sh                  # One-command macOS/Linux auto launcher
├── requirements.txt        # Python package dependencies
├── .env.example            # Environment configuration template
├── .gitignore              # Git ignore rules (protects .env & cache)
├── static/
│   ├── main.css            # Dark/light theme styles
│   ├── main.js             # Voice, chat, audio & slide bar logic
│   ├── faq-manager.js      # FAQ CRUD management
│   ├── data-manager.js     # Product catalog CRUD management
│   └── logs.js             # Call session logs & transcript viewer
├── faq.json                # Seed knowledge base (20 B2B FAQs)
├── products.json           # Seed catalog (21 Wholesale electronics)
├── call_logs.db            # SQLite database (auto-created on first run)
├── view_logs.py            # CLI script to inspect session logs in terminal
├── README.md               # Project documentation
├── REPORT.md               # Technical architecture report
└── TESTING.md              # Complete test matrix & evaluation log
```

---

## 📡 REST API Reference

### Call & AI Dialogue
- `POST /api/chat` — Send caller message; returns AI response, intent, and outcome.
- `POST /api/tts` — Convert text to streaming MP3 audio.
- `GET /api/health` — System status, active model, and database record counts.
- `GET /api/key-status` — Active Gemini API key and failover state.

### FAQ Knowledge Base
- `GET /api/faqs` — Retrieve suggested questions for the slide bar chips.
- `GET /api/faqs/all` — Retrieve all FAQs for the management table.
- `POST /api/faqs` — Create a new FAQ entry (auto-updates AI knowledge base).
- `PUT /api/faqs/{id}` — Update an existing FAQ entry.
- `DELETE /api/faqs/{id}` — Delete a FAQ (automatically resequences IDs 1..N).

### Wholesale Product Catalog
- `GET /api/products` — Retrieve products with optional `?category=` filter.
- `GET /api/products/{id}` — Retrieve a single product by ID.
- `POST /api/products` — Add a new wholesale product.
- `PUT /api/products/{id}` — Update product details, price, or stock status.
- `DELETE /api/products/{id}` — Delete product (automatically resequences IDs 1..N).

### Analytics & Transcripts
- `GET /api/calls` — Recent call session records (`?limit=20`).
- `GET /api/history` — Full chronological transcript for a `session_id`.
- `GET /api/logs` — High-level session analytics and outcome distribution.

---

## ❓ Troubleshooting & FAQs

### 1. Microphone not working?
- Check your browser address bar and verify that microphone permissions are set to **Allow**.
- Voice recognition uses the browser's native **Web Speech API**, which works best in **Google Chrome**, **Microsoft Edge**, and Chromium-based browsers.

### 2. Port 8000 is already in use?
- If port `8000` is occupied by another process, change `PORT=8080` in your `.env` file or terminate the existing process:
  ```powershell
  # Windows PowerShell
  Get-Process -Id (Get-NetTCPConnection -LocalPort 8000).OwningProcess | Stop-Process -Force
  ```

### 3. Gemini Free Tier Quota (`429 ResourceExhausted`)?
- Supply a second API key under `GEMINI_API_KEY_2` in `.env` to enable auto-failover.
- Ensure `GEMINI_MODEL=models/gemini-flash-lite-latest` in `.env` as it has higher free rate limits and ultra-fast inference speed.

---

## 📄 License
This project is open-source and available under the [MIT License](LICENSE).
