# =====================================================================
# AI Calling Agent - FastAPI Backend
# =====================================================================
# Description:
#   A complete voice-enabled AI customer support backend using FastAPI,
#   Google Gemini AI (google-genai modern SDK), and gTTS.
#
# How to run:
#   Windows:  Double-click run.bat (or run 'run.bat' in terminal)
#   Linux/Mac: ./run.sh
#   Manual:   python app.py  (or: uvicorn app:app --reload)
#
# Web Dashboard:
#   http://127.0.0.1:8000
# =====================================================================

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
from pathlib import Path

# Modern official Google GenAI SDK with fallback
try:
    from google import genai
    from google.genai import types, errors
    NEW_GENAI_AVAILABLE = True
except ImportError:
    import google.generativeai as genai  # type: ignore[no-redef]
    types = None  # type: ignore[assignment]
    errors = None  # type: ignore[assignment]
    NEW_GENAI_AVAILABLE = False

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from gtts import gTTS
from pydantic import BaseModel, Field

# =====================================================================
# 1. Configuration and Paths
# =====================================================================
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent


def resolve_db_path() -> Path:
    """Determine a safe and writable path for SQLite database across local and serverless environments."""
    if env_path := (os.getenv("DB_PATH") or os.getenv("DATABASE_PATH")):
        p = Path(env_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    # Running on Vercel, AWS Lambda, or Serverless read-only container
    if os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        tmp_db = Path(tempfile.gettempdir()) / "call_logs.db"
        tmp_db.parent.mkdir(parents=True, exist_ok=True)
        default_local = BASE_DIR / "call_logs.db"
        if default_local.exists() and (not tmp_db.exists() or tmp_db.stat().st_size == 0):
            try:
                shutil.copy2(default_local, tmp_db)
            except Exception:
                pass
        return tmp_db

    # Test if BASE_DIR is writable
    try:
        test_file = BASE_DIR / ".write_test"
        test_file.touch()
        test_file.unlink()
        return BASE_DIR / "call_logs.db"
    except (OSError, PermissionError):
        tmp_db = Path(tempfile.gettempdir()) / "call_logs.db"
        tmp_db.parent.mkdir(parents=True, exist_ok=True)
        default_local = BASE_DIR / "call_logs.db"
        if default_local.exists() and (not tmp_db.exists() or tmp_db.stat().st_size == 0):
            try:
                shutil.copy2(default_local, tmp_db)
            except Exception:
                pass
        return tmp_db


DB_PATH = resolve_db_path()
FAQ_PATH = BASE_DIR / "faq.json"
PRODUCTS_PATH = BASE_DIR / "products.json"
INDEX_HTML = BASE_DIR / "index.html"
STATIC_DIR = BASE_DIR / "static"

MODEL_NAME = os.getenv("GEMINI_MODEL", "models/gemini-flash-lite-latest")
GEMINI_TIMEOUT_S = 25
MAX_HISTORY_TURNS = 6
MAX_SESSIONS = 500

ESCALATION_REPLY = "I am transferring your call to a dedicated Human Support Agent. Please hold on."
ESCALATION_SPEECH = "I am transferring your call to a dedicated Human Support Agent. Please hold on."

FALLBACK_REPLY = (
    "I am currently experiencing a brief delay in accessing the database. "
    "Please try asking again in a few moments or contact our support helpline."
)
FALLBACK_SPEECH = (
    "I am currently experiencing a brief delay. "
    "Please try asking again in a few moments or contact our support helpline."
)

RATE_LIMIT_REPLY = (
    "Our server is handling high call volume right now. "
    "Please stay on the line or contact human support directly."
)
RATE_LIMIT_SPEECH = (
    "Our server is handling high call volume. "
    "Please hold on or reach out to human support directly."
)

SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
ARABIC_SCRIPT = re.compile(r"[\u0600-\u06FF]")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ai_calling_agent")


# =====================================================================
# 2. API Key Manager (Dual Key with Auto-Failover)
# =====================================================================
class ApiKeyManager:
    """Manages primary and secondary Gemini API keys with auto-failover."""

    QUOTA_SIGNALS = ("quota", "resource_exhausted", "429", "resourceexhausted")

    def __init__(self) -> None:
        self.key1: str | None = os.getenv("GEMINI_API_KEY")
        self.key2: str | None = os.getenv("GEMINI_API_KEY_2")

        self.active_index: int = 0  # 0 = primary, 1 = secondary
        self.key1_exhausted: bool = False
        self.key2_exhausted: bool = False
        self.client = None

        if not self.key1:
            log.warning("GEMINI_API_KEY is not set. Please configure it in your .env file.")
        else:
            self._configure_active()

    def _configure_active(self) -> None:
        key = self.key1 if self.active_index == 0 else self.key2
        if not key:
            return
        if NEW_GENAI_AVAILABLE:
            self.client = genai.Client(api_key=key)
        else:
            genai.configure(api_key=key)
        log.info("Gemini configured with %s key.", self.active_label)

    @property
    def active_label(self) -> str:
        return "Primary" if self.active_index == 0 else "Secondary"

    @property
    def primary_status(self) -> str:
        if not self.key1:
            return "not_configured"
        return "exhausted" if self.key1_exhausted else "active"

    @property
    def secondary_status(self) -> str:
        if not self.key2:
            return "not_configured"
        return "exhausted" if self.key2_exhausted else "active"

    @property
    def has_fallback(self) -> bool:
        return bool(self.key2)

    def is_quota_error(self, exc: Exception) -> bool:
        """Return True if the exception looks like a quota or rate-limit error."""
        if errors and isinstance(exc, (errors.APIError, errors.ClientError)):
            if getattr(exc, "code", None) == 429:
                return True
        text = str(exc).lower()
        return any(sig in text for sig in self.QUOTA_SIGNALS)

    def mark_exhausted_and_switch(self) -> bool:
        """Mark current key as exhausted and switch to the backup key if available."""
        if self.active_index == 0:
            self.key1_exhausted = True
            if self.key2 and not self.key2_exhausted:
                log.warning("Primary Gemini key exhausted — switching to Secondary key.")
                self.active_index = 1
                self._configure_active()
                return True
        else:
            self.key2_exhausted = True
            if self.key1 and not self.key1_exhausted:
                log.warning("Secondary Gemini key exhausted — switching to Primary key.")
                self.active_index = 0
                self._configure_active()
                return True

        log.error("All Gemini API keys are exhausted.")
        return False

    def status_dict(self) -> dict:
        return {
            "active_key": self.active_label,
            "primary_status": self.primary_status,
            "secondary_status": self.secondary_status,
            "has_fallback": self.has_fallback,
        }


key_manager = ApiKeyManager()


# =====================================================================
# 3. Database Setup and Seed Data
# =====================================================================
@contextmanager
def db():
    """Context manager for SQLite database connections."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def seed_products() -> None:
    """Seed the products table from products.json if the table is empty."""
    with db() as conn:
        count = conn.execute("SELECT COUNT(*) AS total FROM products").fetchone()["total"]
        if count > 0:
            return
        if not PRODUCTS_PATH.exists():
            log.warning("products.json not found; products table will remain empty.")
            return
        try:
            data = json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))
            items = data.get("products", [])
        except Exception as exc:
            log.error("Failed to parse products.json: %r", exc)
            return
        product_tuples = [
            (
                p.get("name", "Unknown Item"),
                p.get("category", "General"),
                p.get("brand", "Generic"),
                int(p.get("price", 0)),
                p.get("stock_status", "in_stock"),
                p.get("description", ""),
            )
            for p in items
        ]
        if product_tuples:
            conn.executemany(
                "INSERT INTO products (name, category, brand, price, stock_status, description) VALUES (?, ?, ?, ?, ?, ?)",
                product_tuples,
            )
            log.info("Seeded %d products from products.json.", len(product_tuples))


def seed_faqs() -> None:
    """Seed the faqs table from faq.json if the table is empty."""
    with db() as conn:
        count = conn.execute("SELECT COUNT(*) AS total FROM faqs").fetchone()["total"]
        if count > 0:
            return
        if not FAQ_PATH.exists():
            log.warning("faq.json not found; faqs table will remain empty.")
            return
        try:
            data = json.loads(FAQ_PATH.read_text(encoding="utf-8"))
            items = data.get("faqs", [])
        except Exception as exc:
            log.error("Failed to parse faq.json: %r", exc)
            return
        faq_tuples = [
            (
                f.get("intent", "general"),
                f.get("question", ""),
                f.get("answer", ""),
            )
            for f in items
            if f.get("question") and f.get("answer")
        ]
        if faq_tuples:
            conn.executemany(
                "INSERT INTO faqs (intent, question, answer) VALUES (?, ?, ?)",
                faq_tuples,
            )
            log.info("Seeded %d FAQs from faq.json.", len(faq_tuples))


def init_db() -> None:
    """Initialize all database tables and indexes cleanly across all platforms."""
    try:
        with db() as conn:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                try:
                    conn.execute("PRAGMA journal_mode=DELETE")
                except sqlite3.OperationalError:
                    pass

            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT UNIQUE NOT NULL,
                    started_at TEXT NOT NULL,
                    last_activity TEXT,
                    ended_at TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    outcome TEXT NOT NULL DEFAULT 'Resolved',
                    total_messages INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS call_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    call_id INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
                    timestamp TEXT NOT NULL,
                    user_transcript TEXT,
                    ai_response TEXT,
                    detected_intent TEXT,
                    outcome TEXT
                );

                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    brand TEXT NOT NULL,
                    price INTEGER NOT NULL,
                    stock_status TEXT NOT NULL,
                    description TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS faqs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    intent TEXT NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_msgs_call ON call_messages(call_id, id);
                CREATE INDEX IF NOT EXISTS idx_calls_session ON calls(session_id);
                CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
                """
            )
        seed_products()
        seed_faqs()
        resequence_table("products")
        resequence_table("faqs")
    except Exception as exc:
        log.error("Notice during database initialization: %r", exc)


def resequence_table(table_name: str) -> None:
    """Resequence primary key 'id' to be strictly continuous 1..N and reset sqlite_sequence."""
    if table_name not in ("faqs", "products"):
        return
    with db() as conn:
        rows = conn.execute(f"SELECT * FROM {table_name} ORDER BY id ASC").fetchall()
        if not rows:
            conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table_name,))
            return
        cols = [k for k in rows[0].keys() if k != "id"]
        placeholders = ", ".join(["?"] * len(cols))
        col_list = ", ".join(cols)
        conn.execute(f"DELETE FROM {table_name}")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table_name,))
        for r in rows:
            vals = [r[c] for c in cols]
            conn.execute(f"INSERT INTO {table_name} ({col_list}) VALUES ({placeholders})", vals)


def get_or_create_call(conn: sqlite3.Connection, session_id: str) -> int:
    """Get or create a call session row and return its primary key."""
    row = conn.execute("SELECT id FROM calls WHERE session_id = ?", (session_id,)).fetchone()
    if row:
        return row["id"]
    cursor = conn.execute(
        "INSERT INTO calls (session_id, started_at, last_activity) VALUES (?, ?, ?)",
        (session_id, now(), now()),
    )
    return cursor.lastrowid


def save_message(session_id: str, user_text: str, result: dict) -> None:
    """Persist a conversation turn to the database with safe error handling."""
    try:
        with db() as conn:
            call_id = get_or_create_call(conn, session_id)
            conn.execute(
                "INSERT INTO call_messages (call_id, timestamp, user_transcript, ai_response, detected_intent, outcome) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    call_id,
                    now(),
                    user_text,
                    result.get("reply", ""),
                    result.get("intent", ""),
                    result.get("outcome", "Resolved"),
                ),
            )
            conn.execute(
                "UPDATE calls SET last_activity = ?, total_messages = total_messages + 1, outcome = ? WHERE id = ?",
                (now(), result.get("outcome", "Resolved"), call_id),
            )
            log.info("Saved call log turn for session %s (call_id=%d)", session_id, call_id)
    except Exception as exc:
        log.error("Failed to persist call message for session %s: %r", session_id, exc)


def now() -> str:
    """Return current timestamp as a formatted string."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# =====================================================================
# 4. Knowledge Base Loaders & System Instructions
# =====================================================================
def load_faqs_context() -> str:
    """Load all FAQs from the database and format for Gemini."""
    with db() as conn:
        rows = conn.execute("SELECT question, answer FROM faqs ORDER BY id ASC").fetchall()
    if not rows:
        return "(No FAQs available)"
    return "\n\n".join(f"Q: {r['question']}\nA: {r['answer']}" for r in rows)


def load_products_context() -> str:
    """Load all products from the database and format for Gemini."""
    with db() as conn:
        rows = conn.execute(
            "SELECT id, name, category, brand, price, stock_status, description FROM products ORDER BY category ASC, id ASC"
        ).fetchall()
    if not rows:
        return "(No products catalog available)"
    lines = [
        f"- [{r['category']}] {r['name']} by {r['brand']} | "
        f"Price: PKR {r['price']:,} | Stock: {r['stock_status']} | "
        f"Details: {r['description']}"
        for r in rows
    ]
    return "\n".join(lines)


def build_system_instruction() -> str:
    """Build the Gemini system instruction with live FAQ and product context."""
    faq_ctx = load_faqs_context()
    product_ctx = load_products_context()
    return f"""
You are the DigiBuzz AI Calling Agent, providing customer support for DigiBuzz.

KNOWLEDGE BASE:

1. Operational FAQs (policies, delivery, payments):
{faq_ctx}

2. Product Catalog (wholesale products):
{product_ctx}

CRITICAL RULES:
1. BRANDING & GREETING: You are "DigiBuzz AI Calling Agent". Greet callers professionally in English (e.g., "Hello! I am the DigiBuzz AI Calling Agent. How may I assist you today?").
2. KNOWLEDGE ACCURACY: Answer using ONLY the FAQ knowledge base and Product Catalog above (prices in PKR, stock, specs, comparisons, delivery terms).
3. OFF-TOPIC PROTECTION: NEVER answer questions outside DigiBuzz products and services. Politely decline: "I am sorry, I can only assist you with DigiBuzz products and services." Set intent to "off_topic" and outcome to "Resolved".
4. HUMAN ESCALATION: If the user asks about an unlisted item, unknown policy, or explicitly requests a human representative, state you are transferring their call to a Human Support Agent, and set outcome to "Escalated".
5. LANGUAGE & TONE:
   - Reply in clear, concise, professional English.
   - If the caller uses Urdu script or Roman Urdu, respond politely in English or their preferred language.
   - Keep replies short, conversational, and direct (maximum 2 to 3 sentences suitable for a voice call).

Return ONLY a JSON object with these exact keys:
{{
  "response_text": "short reply displayed in the chat interface",
  "speech_text": "clean plain text reply used for text-to-speech audio",
  "intent": "short snake_case intent (e.g., product_inquiry, price_check, delivery_time, human_handoff, off_topic)",
  "outcome": "Resolved" or "Escalated"
}}
"""


legacy_model = None


def get_legacy_model():
    """Instantiate legacy model if google-genai is unavailable."""
    global legacy_model
    if legacy_model is None and not NEW_GENAI_AVAILABLE:
        legacy_model = genai.GenerativeModel(
            model_name=MODEL_NAME,
            system_instruction=build_system_instruction(),
            generation_config={"temperature": 0.2, "max_output_tokens": 1024},
        )
    return legacy_model


def refresh_knowledge_base() -> None:
    """Rebuild Gemini system instructions and invalidate cached state when FAQs or products change."""
    global legacy_model
    legacy_model = None
    log.info("Knowledge base dynamically refreshed with latest database FAQs and products.")


# Initialize DB safely on startup
init_db()


# =====================================================================
# 5. In-Memory Session History and AI Helpers
# =====================================================================
SESSIONS: OrderedDict[str, list[dict]] = OrderedDict()
CONSECUTIVE_ERRORS: int = 0


def remember(session_id: str, user_text: str, reply: str) -> None:
    """Store conversation turn in memory for multi-turn context."""
    hist = SESSIONS.setdefault(session_id, [])
    hist += [{"role": "user", "parts": [user_text]}, {"role": "model", "parts": [reply]}]
    del hist[: -MAX_HISTORY_TURNS * 2]
    SESSIONS.move_to_end(session_id)
    while len(SESSIONS) > MAX_SESSIONS:
        SESSIONS.popitem(last=False)


def parse_model_json(raw: str) -> dict:
    """Extract and parse JSON from Gemini response with forgiving fallback."""
    text = (raw or "").strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    clean_text = text.replace("```json", "").replace("```", "").strip()
    return {
        "response_text": clean_text or "Hello! How can I assist you with DigiBuzz today?",
        "speech_text": clean_text or "Hello! How can I assist you with DigiBuzz today?",
        "intent": "general_inquiry",
        "outcome": "Resolved",
    }


async def _call_gemini_once(session_id: str, user_text: str) -> str:
    """Make a single Gemini API call using either modern google-genai or legacy fallback."""
    if NEW_GENAI_AVAILABLE:
        if not key_manager.client:
            if key_manager.key1 or key_manager.key2:
                key_manager._configure_active()
            if not key_manager.client:
                raise RuntimeError("GEMINI_API_KEY is not configured. Please set GEMINI_API_KEY in environment variables.")

        contents: list[types.Content] = []
        for turn in SESSIONS.get(session_id, []):
            role = "user" if turn.get("role") == "user" else "model"
            parts = turn.get("parts", [""])
            part_text = parts[0] if isinstance(parts, list) and parts else str(parts)
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=part_text)]))

        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_text)]))

        config = types.GenerateContentConfig(
            system_instruction=build_system_instruction(),
            temperature=0.2,
            max_output_tokens=1024,
            response_mime_type="application/json",
        )

        response = await asyncio.wait_for(
            key_manager.client.aio.models.generate_content(
                model=MODEL_NAME,
                contents=contents,
                config=config,
            ),
            timeout=GEMINI_TIMEOUT_S,
        )
        return response.text or ""
    else:
        model = get_legacy_model()
        if not model:
            raise RuntimeError("Gemini model could not be initialized.")
        contents = SESSIONS.get(session_id, []) + [{"role": "user", "parts": [user_text]}]
        response = await asyncio.wait_for(
            model.generate_content_async(contents),
            timeout=GEMINI_TIMEOUT_S,
        )
        return response.text or ""


async def ask_gemini(session_id: str, user_text: str) -> dict:
    """Send user input with session history to Gemini; auto-failover on quota errors."""
    global CONSECUTIVE_ERRORS

    try:
        raw = await _call_gemini_once(session_id, user_text)
    except Exception as exc:
        if key_manager.is_quota_error(exc):
            switched = key_manager.mark_exhausted_and_switch()
            if switched:
                log.info("Retrying Gemini call with %s key.", key_manager.active_label)
                try:
                    raw = await _call_gemini_once(session_id, user_text)
                except Exception as retry_exc:
                    return _build_error_response(retry_exc)
            else:
                return _build_error_response(exc)
        else:
            return _build_error_response(exc)

    CONSECUTIVE_ERRORS = 0
    data = parse_model_json(raw)
    reply = str(data.get("response_text") or "").strip()
    if not reply:
        reply = "Hello! How can I assist you with DigiBuzz today?"

    raw_outcome = str(data.get("outcome", "")).strip().lower()
    outcome = "Escalated" if raw_outcome == "escalated" else "Resolved"

    return {
        "reply": reply,
        "speech": str(data.get("speech_text") or reply).strip(),
        "intent": str(data.get("intent") or "unknown")[:60],
        "outcome": outcome,
    }


def _build_error_response(exc: Exception) -> dict:
    """Build an appropriate error response based on consecutive failure count."""
    global CONSECUTIVE_ERRORS
    CONSECUTIVE_ERRORS += 1
    log.error("Gemini failure (consecutive #%d): %r", CONSECUTIVE_ERRORS, exc)
    if CONSECUTIVE_ERRORS >= 3:
        return {
            "reply": RATE_LIMIT_REPLY,
            "speech": RATE_LIMIT_SPEECH,
            "intent": "rate_limit_fallback",
            "outcome": "Escalated",
        }
    return {
        "reply": FALLBACK_REPLY,
        "speech": FALLBACK_SPEECH,
        "intent": "service_fallback",
        "outcome": "Escalated",
    }


# =====================================================================
# 6. Text-to-Speech (gTTS)
# =====================================================================
@lru_cache(maxsize=256)
def synthesize(text: str, lang: str) -> bytes:
    """Synthesize text into MP3 audio using gTTS with memory caching."""
    buffer = io.BytesIO()
    gTTS(text=text, lang=lang).write_to_fp(buffer)
    return buffer.getvalue()


# =====================================================================
# 7. Pydantic Request/Response Models
# =====================================================================
class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64, pattern=SESSION_ID_PATTERN)
    message: str = Field(min_length=1, max_length=500)


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=600)


class SyncMessage(BaseModel):
    timestamp: str
    user_transcript: str
    ai_response: str
    detected_intent: str = "general_inquiry"
    outcome: str = "Resolved"


class SyncCallRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64, pattern=SESSION_ID_PATTERN)
    started_at: str | None = None
    last_activity: str | None = None
    outcome: str = "Resolved"
    status: str = "active"
    messages: list[SyncMessage] = []


class FAQCreate(BaseModel):
    intent: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=2000)


class FAQUpdate(BaseModel):
    intent: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=2000)


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=100)
    brand: str = Field(min_length=1, max_length=100)
    price: int = Field(ge=0)
    stock_status: str = Field(min_length=1, max_length=50)
    description: str = Field(min_length=0, max_length=1000, default="")


class ProductUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=100)
    brand: str = Field(min_length=1, max_length=100)
    price: int = Field(ge=0)
    stock_status: str = Field(min_length=1, max_length=50)
    description: str = Field(min_length=0, max_length=1000, default="")


# =====================================================================
# 8. FastAPI Application
# =====================================================================
app = FastAPI(
    title="DigiBuzz AI Calling Agent",
    description="Voice-based AI Customer Support Agent powered by Gemini and gTTS.",
    version="2.1.0",
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
async def serve_index():
    """Serve the single-page web dashboard."""
    if INDEX_HTML.exists():
        return FileResponse(INDEX_HTML)
    return {"message": "DigiBuzz AI Calling Agent API is running."}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    favicon_path = BASE_DIR / "favicon.ico"
    if favicon_path.exists():
        return FileResponse(favicon_path)
    raise HTTPException(status_code=404)


# -------------------- Health --------------------
@app.get("/api/health")
async def health_check():
    """Return system health and knowledge base counts."""
    def query():
        with db() as conn:
            faq_count = conn.execute("SELECT COUNT(*) AS n FROM faqs").fetchone()["n"]
            product_count = conn.execute("SELECT COUNT(*) AS n FROM products").fetchone()["n"]
        return faq_count, product_count

    faqs, products = await asyncio.to_thread(query)
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "engine": "google-genai" if NEW_GENAI_AVAILABLE else "google-generativeai",
        "faqs": faqs,
        "products": products,
    }


# -------------------- Key Status --------------------
@app.get("/api/key-status")
async def get_key_status():
    """Return current API key status for monitoring."""
    return key_manager.status_dict()


# -------------------- Chat --------------------
@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    """Process a user message with Gemini AI and log the interaction."""
    text = req.message.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Message is empty.")
    result = await ask_gemini(req.session_id, text)
    remember(req.session_id, text, result["reply"])
    await asyncio.to_thread(save_message, req.session_id, text, result)
    return result


# -------------------- TTS --------------------
@app.post("/api/tts")
async def tts_endpoint(req: TTSRequest):
    """Convert text to spoken MP3 audio via gTTS."""
    lang = "ur" if ARABIC_SCRIPT.search(req.text) else "en"
    try:
        audio = await asyncio.to_thread(synthesize, req.text.strip(), lang)
    except Exception as exc:
        log.error("TTS failure: %r", exc)
        raise HTTPException(status_code=502, detail="Text-to-speech is unavailable.")
    return Response(
        audio,
        media_type="audio/mpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


# -------------------- FAQ Endpoints --------------------
@app.get("/api/faqs")
async def get_faq_chips(limit: int = Query(default=18, ge=1, le=50)):
    """Return top suggested questions as quick-click slider chips."""
    def query():
        with db() as conn:
            rows = conn.execute("SELECT question, intent FROM faqs ORDER BY id ASC LIMIT ?", (limit,)).fetchall()
            return [{"question": r["question"], "intent": r["intent"]} for r in rows]

    return {"questions": await asyncio.to_thread(query)}


@app.get("/api/faqs/all")
async def get_all_faqs():
    """Return all FAQs with full details for the management table."""
    def query():
        with db() as conn:
            rows = conn.execute(
                "SELECT id, intent, question, answer FROM faqs ORDER BY id ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    return await asyncio.to_thread(query)


@app.post("/api/faqs", status_code=201)
async def create_faq(faq: FAQCreate):
    """Add a new FAQ entry."""
    def query():
        with db() as conn:
            cursor = conn.execute(
                "INSERT INTO faqs (intent, question, answer) VALUES (?, ?, ?)",
                (faq.intent.strip(), faq.question.strip(), faq.answer.strip()),
            )
            row = conn.execute(
                "SELECT id, intent, question, answer FROM faqs WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            return dict(row)

    res = await asyncio.to_thread(query)
    try:
        refresh_knowledge_base()
    except Exception as exc:
        log.warning("Non-blocking notice refreshing knowledge base after FAQ create: %r", exc)
    return res


@app.put("/api/faqs/{faq_id}")
async def update_faq(faq_id: int, faq: FAQUpdate):
    """Update an existing FAQ entry."""
    def query():
        with db() as conn:
            conn.execute(
                "UPDATE faqs SET intent = ?, question = ?, answer = ? WHERE id = ?",
                (faq.intent.strip(), faq.question.strip(), faq.answer.strip(), faq_id),
            )
            row = conn.execute(
                "SELECT id, intent, question, answer FROM faqs WHERE id = ?", (faq_id,)
            ).fetchone()
            return dict(row) if row else None

    result = await asyncio.to_thread(query)
    if result is None:
        raise HTTPException(status_code=404, detail="FAQ not found.")
    try:
        refresh_knowledge_base()
    except Exception as exc:
        log.warning("Non-blocking notice refreshing knowledge base after FAQ update: %r", exc)
    return result


@app.delete("/api/faqs/{faq_id}", status_code=204)
async def delete_faq(faq_id: int):
    """Delete a FAQ entry by ID and resequence IDs to maintain continuous 1..N numbering."""
    def query():
        with db() as conn:
            row = conn.execute("SELECT id FROM faqs WHERE id = ?", (int(faq_id),)).fetchone()
            if not row:
                return False
            conn.execute("DELETE FROM faqs WHERE id = ?", (int(faq_id),))
            return True

    found = await asyncio.to_thread(query)
    if not found:
        raise HTTPException(status_code=404, detail="FAQ not found.")

    await asyncio.to_thread(resequence_table, "faqs")

    try:
        refresh_knowledge_base()
    except Exception as exc:
        log.warning("Non-blocking notice refreshing knowledge base after delete: %r", exc)

    return Response(status_code=204)


# -------------------- Product Endpoints --------------------
@app.get("/api/products")
async def get_products(category: str | None = Query(default=None)):
    """Return all products, with optional category filter."""
    def query():
        with db() as conn:
            if category and category.strip():
                rows = conn.execute(
                    "SELECT id, name, category, brand, price, stock_status, description FROM products WHERE LOWER(category) = LOWER(?) ORDER BY id ASC",
                    (category.strip(),),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, name, category, brand, price, stock_status, description FROM products ORDER BY id ASC"
                ).fetchall()
            return [dict(r) for r in rows]

    return await asyncio.to_thread(query)


@app.get("/api/products/{product_id}")
async def get_single_product(product_id: int):
    """Return a single product by ID."""
    def query():
        with db() as conn:
            row = conn.execute(
                "SELECT id, name, category, brand, price, stock_status, description FROM products WHERE id = ?",
                (int(product_id),),
            ).fetchone()
            return dict(row) if row else None

    product = await asyncio.to_thread(query)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    return product


@app.post("/api/products", status_code=201)
async def create_product(product: ProductCreate):
    """Add a new product to the catalog."""
    def query():
        with db() as conn:
            cursor = conn.execute(
                "INSERT INTO products (name, category, brand, price, stock_status, description) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    product.name.strip(),
                    product.category.strip(),
                    product.brand.strip(),
                    product.price,
                    product.stock_status.strip(),
                    product.description.strip(),
                ),
            )
            row = conn.execute(
                "SELECT id, name, category, brand, price, stock_status, description FROM products WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            return dict(row)

    res = await asyncio.to_thread(query)
    try:
        refresh_knowledge_base()
    except Exception as exc:
        log.warning("Non-blocking notice refreshing knowledge base after product create: %r", exc)
    return res


@app.put("/api/products/{product_id}")
async def update_product(product_id: int, product: ProductUpdate):
    """Update an existing product."""
    def query():
        with db() as conn:
            conn.execute(
                "UPDATE products SET name = ?, category = ?, brand = ?, price = ?, stock_status = ?, description = ? WHERE id = ?",
                (
                    product.name.strip(),
                    product.category.strip(),
                    product.brand.strip(),
                    product.price,
                    product.stock_status.strip(),
                    product.description.strip(),
                    int(product_id),
                ),
            )
            row = conn.execute(
                "SELECT id, name, category, brand, price, stock_status, description FROM products WHERE id = ?",
                (int(product_id),),
            ).fetchone()
            return dict(row) if row else None

    result = await asyncio.to_thread(query)
    if result is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    try:
        refresh_knowledge_base()
    except Exception as exc:
        log.warning("Non-blocking notice refreshing knowledge base after product update: %r", exc)
    return result


@app.delete("/api/products/{product_id}", status_code=204)
async def delete_product(product_id: int):
    """Delete a product by ID and resequence IDs to maintain continuous 1..N numbering."""
    def query():
        with db() as conn:
            row = conn.execute("SELECT id FROM products WHERE id = ?", (int(product_id),)).fetchone()
            if not row:
                return False
            conn.execute("DELETE FROM products WHERE id = ?", (int(product_id),))
            return True

    found = await asyncio.to_thread(query)
    if not found:
        raise HTTPException(status_code=404, detail="Product not found.")

    await asyncio.to_thread(resequence_table, "products")

    try:
        refresh_knowledge_base()
    except Exception as exc:
        log.warning("Non-blocking notice refreshing knowledge base after product delete: %r", exc)
    return Response(status_code=204)


# -------------------- Call Log Endpoints --------------------
@app.post("/api/calls/sync")
async def sync_client_calls(req: SyncCallRequest):
    """Sync client-side recorded session logs into SQLite database."""
    def _sync():
        with db() as conn:
            call_id = get_or_create_call(conn, req.session_id)
            for m in req.messages:
                exists = conn.execute(
                    "SELECT 1 FROM call_messages WHERE call_id = ? AND timestamp = ? AND user_transcript = ?",
                    (call_id, m.timestamp, m.user_transcript),
                ).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT INTO call_messages (call_id, timestamp, user_transcript, ai_response, detected_intent, outcome) VALUES (?, ?, ?, ?, ?, ?)",
                        (call_id, m.timestamp, m.user_transcript, m.ai_response, m.detected_intent, m.outcome),
                    )
            msg_count = conn.execute("SELECT COUNT(*) AS n FROM call_messages WHERE call_id = ?", (call_id,)).fetchone()["n"]
            conn.execute(
                "UPDATE calls SET last_activity = ?, total_messages = ?, outcome = ? WHERE id = ?",
                (req.last_activity or now(), msg_count, req.outcome, call_id),
            )
        return {"status": "synced", "session_id": req.session_id}

    try:
        return await asyncio.to_thread(_sync)
    except Exception as exc:
        log.error("Call sync failed: %r", exc)
        return {"status": "error", "detail": str(exc)}


@app.get("/api/calls")
async def get_recent_calls(limit: int = Query(default=20, ge=1, le=100)):
    """Return recent call sessions for the call logs dashboard."""
    def query():
        with db() as conn:
            rows = conn.execute(
                "SELECT session_id, started_at, last_activity, status, outcome, total_messages FROM calls ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    return await asyncio.to_thread(query)


@app.get("/api/history")
async def get_conversation_history(
    session_id: str = Query(..., pattern=SESSION_ID_PATTERN),
):
    """Return full conversation transcript for a session."""
    def query():
        with db() as conn:
            rows = conn.execute(
                "SELECT id, timestamp, user_transcript, ai_response, detected_intent, outcome FROM call_messages WHERE call_id = (SELECT id FROM calls WHERE session_id = ?) ORDER BY id ASC",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    return await asyncio.to_thread(query)


@app.get("/api/logs")
async def get_session_logs(
    session_id: str = Query(..., pattern=SESSION_ID_PATTERN),
    limit: int = Query(default=20, ge=1, le=100),
):
    """Return message history and metrics for a specific session."""
    def query():
        with db() as conn:
            rows = conn.execute(
                "SELECT timestamp, user_transcript, ai_response, detected_intent, outcome FROM call_messages WHERE call_id = (SELECT id FROM calls WHERE session_id = ?) ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
            outcome_stats = conn.execute(
                "SELECT outcome, COUNT(*) AS n FROM call_messages WHERE call_id = (SELECT id FROM calls WHERE session_id = ?) GROUP BY outcome",
                (session_id,),
            ).fetchall()
            intent_stats = conn.execute(
                "SELECT detected_intent, COUNT(*) AS n FROM call_messages WHERE call_id = (SELECT id FROM calls WHERE session_id = ?) GROUP BY detected_intent",
                (session_id,),
            ).fetchall()
        outcomes = {r["outcome"]: r["n"] for r in outcome_stats}
        intents = {r["detected_intent"]: r["n"] for r in intent_stats}
        product_queries = sum(v for k, v in intents.items() if "product" in (k or "").lower() or "price" in (k or "").lower())
        human_handoffs = sum(v for k, v in intents.items() if "human" in (k or "").lower() or "handoff" in (k or "").lower())
        delivery_queries = sum(v for k, v in intents.items() if "delivery" in (k or "").lower() or "order" in (k or "").lower())
        return (
            [dict(r) for r in rows],
            sum(outcomes.values()),
            outcomes.get("Resolved", 0),
            outcomes.get("Escalated", 0),
            product_queries,
            human_handoffs,
            delivery_queries,
        )

    rows, total, resolved, escalated, products, handoffs, deliveries = await asyncio.to_thread(query)
    return {
        "rows": rows,
        "total": total,
        "resolved": resolved,
        "escalated": escalated,
        "product_inquiries": products,
        "human_handoffs": handoffs,
        "delivery_inquiries": deliveries,
    }


# =====================================================================
# 9. Main Server Entrypoint
# =====================================================================
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
