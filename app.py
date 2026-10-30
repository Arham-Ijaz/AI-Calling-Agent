# =====================================================================
# AI Calling Agent - FastAPI Multi-Tenant SaaS Backend
# =====================================================================
# Description:
#   A complete voice-enabled AI customer support SaaS backend featuring:
#   - Google Gemini AI (modern official google-genai SDK)
#   - Text-to-Speech (gTTS)
#   - Multi-Tenant Role-Based Access Control (RBAC: Admin vs User)
#   - OWASP Top 10 Security Compliance:
#     * Strict Tenant Data Isolation (No IDOR)
#     * Bcrypt Password Hashing with Salt
#     * Signed JWT Session Cookies (HttpOnly, Secure, SameSite=Lax)
#     * Brute-Force Rate Limiting & Account Suspension
#     * 100% Parameterized SQLite Queries (SQL Injection Prevention)
#     * Security Headers Middleware (X-Content-Type-Options, X-Frame-Options, etc.)
#   - Cloud & Serverless compatibility (Vercel, AWS Lambda, local)
#
# Web Dashboard:
#   http://127.0.0.1:8000
# =====================================================================

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
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
from functools import lru_cache
from pathlib import Path

import bcrypt
import jwt
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from gtts import gTTS
from pydantic import BaseModel, EmailStr, Field

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

JWT_SECRET = os.getenv("JWT_SECRET", "digibuzz_saas_secure_jwt_secret_key_2026_super_safe_and_random")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_DAYS = 7
COOKIE_NAME = "digibuzz_session"
IS_PRODUCTION = bool(os.getenv("VERCEL") == "1" or os.getenv("ENVIRONMENT") == "production")

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
PHONE_PATTERN = r"^\+?[0-9\s\-()]{10,20}$"
EMAIL_PATTERN = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
ARABIC_SCRIPT = re.compile(r"[\u0600-\u06FF]")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ai_calling_agent")


# =====================================================================
# 2. Security & Cryptography Helpers (OWASP Top 10 A02 / A07)
# =====================================================================
def hash_password(plain_password: str) -> str:
    """Hash password securely using bcrypt with automatic salt."""
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt(12)).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against bcrypt hash."""
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


def create_access_token(user_id: int, role: str, email: str) -> str:
    """Generate signed HS256 JWT access token."""
    expire = datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRATION_DAYS)
    payload = {
        "sub": str(user_id),
        "role": role,
        "email": email,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """Validate and decode signed JWT token."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except (jwt.PyJWTError, Exception):
        return None


# Brute force login prevention: max 5 failed attempts per IP/identifier in 15 mins
LOGIN_ATTEMPTS: dict[str, list[float]] = {}


def check_login_rate_limit(key: str) -> None:
    now_ts = datetime.now().timestamp()
    history = LOGIN_ATTEMPTS.get(key, [])
    valid_attempts = [t for t in history if now_ts - t < 900]
    LOGIN_ATTEMPTS[key] = valid_attempts
    if len(valid_attempts) >= 5:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Please wait 15 minutes before trying again.",
        )


def record_login_failure(key: str) -> None:
    LOGIN_ATTEMPTS.setdefault(key, []).append(datetime.now().timestamp())


def clear_login_failures(key: str) -> None:
    LOGIN_ATTEMPTS.pop(key, None)


# =====================================================================
# 3. API Key Manager (Dual Key with Auto-Failover)
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
        """Return True if exception looks like a quota or rate-limit error."""
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
# 4. Database Setup, Migrations and Seed Data
# =====================================================================
@contextmanager
def db():
    """Context manager for SQLite database connections."""
    conn = sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def seed_products() -> None:
    """Seed the products table from products.json if empty."""
    with db() as conn:
        count = conn.execute("SELECT COUNT(*) AS total FROM products").fetchone()["total"]
        if count > 0:
            return
        if not PRODUCTS_PATH.exists():
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
                None,  # Platform default (no owner)
            )
            for p in items
        ]
        if product_tuples:
            conn.executemany(
                "INSERT INTO products (name, category, brand, price, stock_status, description, user_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                product_tuples,
            )
            log.info("Seeded %d platform products.", len(product_tuples))


def seed_faqs() -> None:
    """Seed the faqs table from faq.json if empty."""
    with db() as conn:
        count = conn.execute("SELECT COUNT(*) AS total FROM faqs").fetchone()["total"]
        if count > 0:
            return
        if not FAQ_PATH.exists():
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
                None,  # Platform default (no owner)
            )
            for f in items
            if f.get("question") and f.get("answer")
        ]
        if faq_tuples:
            conn.executemany(
                "INSERT INTO faqs (intent, question, answer, user_id) VALUES (?, ?, ?, ?)",
                faq_tuples,
            )
            log.info("Seeded %d platform FAQs.", len(faq_tuples))


PRIMARY_ADMIN_EMAIL = os.getenv("DEFAULT_ADMIN_EMAIL", "admin@digibuzz.com").strip().lower()


def is_primary_admin(user_id: int | None = None, email: str | None = None) -> bool:
    """Check if the given user_id or email corresponds to the Primary Super Admin."""
    if user_id == 1:
        return True
    if email and email.strip().lower() == PRIMARY_ADMIN_EMAIL:
        return True
    try:
        with db() as conn:
            # The earliest created admin account is the primary platform super admin
            row = conn.execute(
                "SELECT id, email FROM users WHERE LOWER(email) = ? OR role = 'admin' ORDER BY id ASC LIMIT 1",
                (PRIMARY_ADMIN_EMAIL,),
            ).fetchone()
            if row:
                if user_id is not None and row["id"] == user_id:
                    return True
                if email and row["email"].strip().lower() == email.strip().lower():
                    return True
    except Exception:
        pass
    return False


def seed_default_admin() -> None:
    """Ensure at least one admin account exists."""
    with db() as conn:
        admin = conn.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone()
        if not admin:
            admin_email = PRIMARY_ADMIN_EMAIL
            admin_phone = os.getenv("DEFAULT_ADMIN_PHONE", "+923001234567").strip()
            admin_pass = os.getenv("DEFAULT_ADMIN_PASSWORD", "Admin@12345")
            hashed = hash_password(admin_pass)
            conn.execute(
                "INSERT INTO users (email, phone, full_name, password_hash, role, is_active, created_at) VALUES (?, ?, ?, ?, 'admin', 1, ?)",
                (admin_email, admin_phone, "Platform Administrator", hashed, now()),
            )
            log.info("Default Admin account created: %s (Password: %s)", admin_email, admin_pass)


def init_db() -> None:
    """Initialize all database tables, columns, indexes and seed default admin."""
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
                -- Users table (OWASP A01 / A07 Multi-tenant RBAC)
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE NOT NULL,
                    phone TEXT UNIQUE NOT NULL,
                    full_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    last_login TEXT
                );

                CREATE TABLE IF NOT EXISTS calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT UNIQUE NOT NULL,
                    started_at TEXT NOT NULL,
                    last_activity TEXT,
                    ended_at TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    outcome TEXT NOT NULL DEFAULT 'Resolved',
                    total_messages INTEGER NOT NULL DEFAULT 0,
                    user_id INTEGER REFERENCES users(id)
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
                    description TEXT NOT NULL,
                    user_id INTEGER REFERENCES users(id)
                );

                CREATE TABLE IF NOT EXISTS faqs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    intent TEXT NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    user_id INTEGER REFERENCES users(id)
                );

                CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
                CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone);
                CREATE INDEX IF NOT EXISTS idx_calls_session ON calls(session_id);
                CREATE INDEX IF NOT EXISTS idx_msgs_call ON call_messages(call_id, id);
                """
            )

            # Column migrations if database was created by previous version
            for tbl in ("calls", "products", "faqs"):
                try:
                    conn.execute(f"ALTER TABLE {tbl} ADD COLUMN user_id INTEGER REFERENCES users(id)")
                except sqlite3.OperationalError:
                    pass

            # Create tenant indexes after column migrations
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_calls_user ON calls(user_id);
                CREATE INDEX IF NOT EXISTS idx_products_user ON products(user_id);
                CREATE INDEX IF NOT EXISTS idx_faqs_user ON faqs(user_id);
                """
            )

        seed_products()
        seed_faqs()
        seed_default_admin()

        # Automatically defragment & re-sequence product and FAQ IDs on startup
        with db() as conn:
            resequence_table(conn, "products")
            resequence_table(conn, "faqs")
    except Exception as exc:
        log.error("Notice during database initialization: %r", exc)


def resequence_table(conn: sqlite3.Connection, table_name: str) -> None:
    """
    Resequence primary keys in a table consecutively (1, 2, 3...)
    and synchronize sqlite_sequence so that new insertions always receive
    the exact next sequential ID (e.g. 21 -> 22, and if 22 is deleted,
    the next item is 22, never skipping numbers).
    """
    rows = conn.execute(f"SELECT id FROM {table_name} ORDER BY id ASC").fetchall()
    if not rows:
        conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table_name,))
        conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES (?, 0)", (table_name,))
        return
    # Two-phase ID assignment to prevent SQLite UNIQUE constraint collisions
    for i, row in enumerate(rows, start=1):
        conn.execute(f"UPDATE {table_name} SET id = ? WHERE id = ?", (-i, row["id"]))
    for i in range(1, len(rows) + 1):
        conn.execute(f"UPDATE {table_name} SET id = ? WHERE id = ?", (i, -i))
    max_id = len(rows)
    conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table_name,))
    conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)", (table_name, max_id))


def get_or_create_call(conn: sqlite3.Connection, session_id: str, user_id: int | None = None) -> int:
    """Get or create a call session row and return its primary key."""
    row = conn.execute("SELECT id, user_id FROM calls WHERE session_id = ?", (session_id,)).fetchone()
    if row:
        if user_id is not None and row["user_id"] is None:
            conn.execute("UPDATE calls SET user_id = ? WHERE id = ?", (user_id, row["id"]))
        return row["id"]
    cursor = conn.execute(
        "INSERT INTO calls (session_id, started_at, last_activity, user_id) VALUES (?, ?, ?, ?)",
        (session_id, now(), now(), user_id),
    )
    return cursor.lastrowid


def save_message(session_id: str, user_text: str, result: dict, user_id: int | None = None) -> None:
    """Persist conversation turn to the database with safe error handling."""
    try:
        with db() as conn:
            call_id = get_or_create_call(conn, session_id, user_id)
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
    except Exception as exc:
        log.error("Failed to persist call message for session %s: %r", session_id, exc)


def now() -> str:
    """Return current timestamp as a formatted string."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# =====================================================================
# 5. Knowledge Base Loaders & Dynamic System Instructions
# =====================================================================
def load_faqs_context(user_id: int | None = None) -> str:
    """Load platform FAQs + admin-created FAQs + custom user FAQs from database."""
    with db() as conn:
        if user_id:
            rows = conn.execute(
                """
                SELECT question, answer FROM faqs
                WHERE user_id IS NULL
                   OR user_id IN (SELECT id FROM users WHERE role = 'admin')
                   OR user_id = ?
                ORDER BY id ASC
                """,
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT question, answer FROM faqs
                WHERE user_id IS NULL
                   OR user_id IN (SELECT id FROM users WHERE role = 'admin')
                ORDER BY id ASC
                """
            ).fetchall()
    if not rows:
        return "(No FAQs available)"
    return "\n\n".join(f"Q: {r['question']}\nA: {r['answer']}" for r in rows)


def load_products_context(user_id: int | None = None) -> str:
    """Load platform products + admin-created products + custom user products from database."""
    with db() as conn:
        if user_id:
            rows = conn.execute(
                """
                SELECT id, name, category, brand, price, stock_status, description FROM products
                WHERE user_id IS NULL
                   OR user_id IN (SELECT id FROM users WHERE role = 'admin')
                   OR user_id = ?
                ORDER BY category ASC, id ASC
                """,
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, name, category, brand, price, stock_status, description FROM products
                WHERE user_id IS NULL
                   OR user_id IN (SELECT id FROM users WHERE role = 'admin')
                ORDER BY category ASC, id ASC
                """
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


def build_system_instruction(user_id: int | None = None) -> str:
    """Build the Gemini system instruction with scoped FAQ and product context."""
    faq_ctx = load_faqs_context(user_id)
    product_ctx = load_products_context(user_id)
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
3. REAL-TIME SYNCHRONIZATION: Your knowledge base above is live and constantly synchronized with the store database. Whenever a caller asks about any item, product, brand, warranty, or policy, always consult the current Product Catalog and Operational FAQs above. If the item or policy is listed above, state its exact name, specifications, stock status, and wholesale price in PKR immediately.
4. OFF-TOPIC PROTECTION: NEVER answer questions outside DigiBuzz products, wholesale orders, and store policies. Politely decline: "I am sorry, I can only assist you with DigiBuzz products and services." Set intent to "off_topic" and outcome to "Resolved".
5. HUMAN ESCALATION: If the user asks about an unlisted item, unknown policy, or explicitly requests a human representative, state you are transferring their call to a Human Support Agent, and set outcome to "Escalated".
6. LANGUAGE & TONE:
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


def get_legacy_model(user_id: int | None = None):
    """Instantiate legacy model if google-genai is unavailable."""
    global legacy_model
    if legacy_model is None and not NEW_GENAI_AVAILABLE:
        legacy_model = genai.GenerativeModel(
            model_name=MODEL_NAME,
            system_instruction=build_system_instruction(user_id),
            generation_config={"temperature": 0.2, "max_output_tokens": 1024},
        )
    return legacy_model


def refresh_knowledge_base() -> None:
    """Rebuild Gemini system instructions and invalidate cached state when FAQs or products change."""
    global legacy_model
    legacy_model = None
    log.info("Knowledge base dynamically refreshed.")


# Initialize DB safely on startup
init_db()


# =====================================================================
# 6. In-Memory Session History and AI Helpers
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


async def _call_gemini_once(session_id: str, user_text: str, user_id: int | None = None) -> str:
    """Make a single Gemini API call using either modern google-genai or legacy fallback."""
    if NEW_GENAI_AVAILABLE:
        if not key_manager.client:
            if key_manager.key1 or key_manager.key2:
                key_manager._configure_active()
            if not key_manager.client:
                raise RuntimeError("GEMINI_API_KEY is not configured.")

        contents: list[types.Content] = []
        for turn in SESSIONS.get(session_id, []):
            role = "user" if turn.get("role") == "user" else "model"
            parts = turn.get("parts", [""])
            part_text = parts[0] if isinstance(parts, list) and parts else str(parts)
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=part_text)]))

        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_text)]))

        config = types.GenerateContentConfig(
            system_instruction=build_system_instruction(user_id),
            temperature=0.2,
            max_output_tokens=1024,
            response_mime_type="application/json",
        )

        try:
            response = await asyncio.wait_for(
                key_manager.client.aio.models.generate_content(
                    model=MODEL_NAME,
                    contents=contents,
                    config=config,
                ),
                timeout=GEMINI_TIMEOUT_S,
            )
            return response.text or ""
        except RuntimeError as r_err:
            if "Event loop" in str(r_err):
                key_manager._configure_active()
                response = await asyncio.wait_for(
                    key_manager.client.aio.models.generate_content(
                        model=MODEL_NAME,
                        contents=contents,
                        config=config,
                    ),
                    timeout=GEMINI_TIMEOUT_S,
                )
                return response.text or ""
            raise
    else:
        model = get_legacy_model(user_id)
        if not model:
            raise RuntimeError("Gemini model could not be initialized.")
        contents = SESSIONS.get(session_id, []) + [{"role": "user", "parts": [user_text]}]
        response = await asyncio.wait_for(
            model.generate_content_async(contents),
            timeout=GEMINI_TIMEOUT_S,
        )
        return response.text or ""


async def ask_gemini(session_id: str, user_text: str, user_id: int | None = None) -> dict:
    """Send user input with session history to Gemini; auto-failover on quota errors."""
    global CONSECUTIVE_ERRORS

    try:
        raw = await _call_gemini_once(session_id, user_text, user_id)
    except Exception as exc:
        if key_manager.is_quota_error(exc):
            switched = key_manager.mark_exhausted_and_switch()
            if switched:
                log.info("Retrying Gemini call with %s key.", key_manager.active_label)
                try:
                    raw = await _call_gemini_once(session_id, user_text, user_id)
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
# 7. Text-to-Speech (gTTS)
# =====================================================================
@lru_cache(maxsize=256)
def synthesize(text: str, lang: str) -> bytes:
    """Synthesize text into MP3 audio using gTTS with memory caching."""
    buffer = io.BytesIO()
    gTTS(text=text, lang=lang).write_to_fp(buffer)
    return buffer.getvalue()


# =====================================================================
# 8. Pydantic Request/Response Models
# =====================================================================
class UserRegister(BaseModel):
    full_name: str = Field(min_length=2, max_length=100)
    email: str = Field(pattern=EMAIL_PATTERN, min_length=5, max_length=120)
    phone: str = Field(pattern=PHONE_PATTERN)
    password: str = Field(min_length=8, max_length=128)


class UserLogin(BaseModel):
    identifier: str = Field(min_length=3, max_length=100)  # email or phone
    password: str = Field(min_length=1, max_length=128)


class UserStatusUpdate(BaseModel):
    is_active: bool


class UserRoleUpdate(BaseModel):
    role: str = Field(pattern=r"^(admin|user)$")


class AdminUserCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=100)
    email: str = Field(pattern=EMAIL_PATTERN, min_length=5, max_length=120)
    phone: str = Field(pattern=PHONE_PATTERN)
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(default="user", pattern=r"^(admin|user)$")
    is_active: bool = True


class AdminUserUpdate(BaseModel):
    full_name: str = Field(min_length=2, max_length=100)
    email: str = Field(pattern=EMAIL_PATTERN, min_length=5, max_length=120)
    phone: str = Field(pattern=PHONE_PATTERN)
    password: str | None = Field(default=None, max_length=128)
    role: str = Field(default="user", pattern=r"^(admin|user)$")
    is_active: bool = True


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
# 9. Authentication Dependencies (OWASP Top 10 A01 / A07)
# =====================================================================
async def get_optional_user(request: Request) -> dict | None:
    """Extract authenticated user if session cookie or Bearer token is present, else None."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        auth_hdr = request.headers.get("Authorization")
        if auth_hdr and auth_hdr.startswith("Bearer "):
            token = auth_hdr[7:].strip()
    if not token:
        return None

    payload = decode_access_token(token)
    if not payload:
        return None

    user_id = payload.get("sub")
    if not user_id:
        return None

    with db() as conn:
        row = conn.execute(
            "SELECT id, email, phone, full_name, role, is_active FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not row or not row["is_active"]:
            return None
        return dict(row)


async def get_current_user(request: Request) -> dict:
    """Enforce authentication on protected endpoints."""
    user = await get_optional_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please sign in to continue.",
        )
    return user


async def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """Enforce Admin authorization (OWASP A01 Role-Based Access Control)."""
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Administrator privileges required.",
        )
    return current_user


# =====================================================================
# 10. FastAPI Application Setup & Security Middleware
# =====================================================================
app = FastAPI(
    title="DigiBuzz AI Calling Agent",
    description="Multi-tenant Voice-based AI Customer Support Platform powered by Gemini and gTTS.",
    version="3.0.0",
)


# OWASP Top 10 A05: Security Headers Middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


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


# =====================================================================
# 11. Authentication Endpoints (Register, Login, Logout, Me)
# =====================================================================
@app.post("/api/auth/register", status_code=201)
async def register_user(req: UserRegister, response: Response):
    """Register a new user account with strict uniqueness checks on email and phone."""
    clean_email = req.email.strip().lower()
    clean_phone = re.sub(r"[\s\-()]", "", req.phone.strip())

    if not re.search(r"[A-Za-z]", req.password) or not re.search(r"[0-9]", req.password):
        raise HTTPException(
            status_code=400,
            detail="Password must be at least 8 characters and contain both letters and numbers.",
        )

    with db() as conn:
        # Check duplicate email
        if conn.execute("SELECT 1 FROM users WHERE LOWER(email) = ?", (clean_email,)).fetchone():
            raise HTTPException(
                status_code=409,
                detail="An account with this email address already exists. Please sign in.",
            )
        # Check duplicate phone
        if conn.execute("SELECT 1 FROM users WHERE phone = ?", (clean_phone,)).fetchone():
            raise HTTPException(
                status_code=409,
                detail="An account with this phone number already exists. Please sign in.",
            )

        password_hash = hash_password(req.password)
        cursor = conn.execute(
            "INSERT INTO users (email, phone, full_name, password_hash, role, is_active, created_at) VALUES (?, ?, ?, ?, 'user', 1, ?)",
            (clean_email, clean_phone, req.full_name.strip(), password_hash, now()),
        )
        new_id = cursor.lastrowid

    token = create_access_token(new_id, "user", clean_email)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=86400 * JWT_EXPIRATION_DAYS,
        httponly=True,
        samesite="lax",
        secure=IS_PRODUCTION,
        path="/",
    )

    return {
        "status": "ok",
        "user": {
            "id": new_id,
            "email": clean_email,
            "phone": clean_phone,
            "full_name": req.full_name.strip(),
            "role": "user",
        },
        "token": token,
    }


@app.post("/api/auth/login")
async def login_user(req: UserLogin, request: Request, response: Response):
    """Authenticate user with email or phone and password, sets secure session cookie."""
    client_ip = request.client.host if request.client else "unknown"
    rate_key = f"{client_ip}:{req.identifier.strip().lower()}"
    check_login_rate_limit(rate_key)

    ident = req.identifier.strip()
    ident_email = ident.lower()
    ident_phone = re.sub(r"[\s\-()]", "", ident)

    with db() as conn:
        row = conn.execute(
            "SELECT id, email, phone, full_name, password_hash, role, is_active FROM users WHERE LOWER(email) = ? OR phone = ? LIMIT 1",
            (ident_email, ident_phone),
        ).fetchone()

    if not row or not verify_password(req.password, row["password_hash"]):
        record_login_failure(rate_key)
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials. Please verify your email/phone and password.",
        )

    if not row["is_active"]:
        raise HTTPException(
            status_code=403,
            detail="Your account has been suspended. Please contact platform support.",
        )

    clear_login_failures(rate_key)

    # Update last login
    with db() as conn:
        conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (now(), row["id"]))

    token = create_access_token(row["id"], row["role"], row["email"])
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=86400 * JWT_EXPIRATION_DAYS,
        httponly=True,
        samesite="lax",
        secure=IS_PRODUCTION,
        path="/",
    )

    return {
        "status": "ok",
        "user": {
            "id": row["id"],
            "email": row["email"],
            "phone": row["phone"],
            "full_name": row["full_name"],
            "role": row["role"],
        },
        "token": token,
    }


@app.post("/api/auth/logout")
async def logout_user(response: Response):
    """Clear session cookie and log user out with full security flags."""
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=IS_PRODUCTION,
    )
    return {"status": "ok", "message": "Signed out successfully."}


@app.get("/api/auth/me")
async def get_my_profile(current_user: dict = Depends(get_current_user)):
    """Return authenticated user profile and permissions."""
    return {"user": current_user}


# =====================================================================
# 12. Admin Management Endpoints (OWASP Top 10 A01 RBAC)
# =====================================================================
@app.get("/api/admin/metrics")
async def get_admin_metrics(_admin: dict = Depends(require_admin)):
    """System-wide SaaS metrics for Admin dashboard."""
    def query():
        with db() as conn:
            total_users = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
            active_users = conn.execute("SELECT COUNT(*) AS n FROM users WHERE is_active = 1").fetchone()["n"]
            suspended_users = total_users - active_users
            total_calls = conn.execute("SELECT COUNT(*) AS n FROM calls").fetchone()["n"]
            total_messages = conn.execute("SELECT COUNT(*) AS n FROM call_messages").fetchone()["n"]
            resolved = conn.execute("SELECT COUNT(*) AS n FROM calls WHERE outcome = 'Resolved'").fetchone()["n"]
            escalated = conn.execute("SELECT COUNT(*) AS n FROM calls WHERE outcome = 'Escalated'").fetchone()["n"]
            total_faqs = conn.execute("SELECT COUNT(*) AS n FROM faqs").fetchone()["n"]
            total_products = conn.execute("SELECT COUNT(*) AS n FROM products").fetchone()["n"]
        return {
            "total_users": total_users,
            "active_users": active_users,
            "suspended_users": suspended_users,
            "total_calls": total_calls,
            "total_messages": total_messages,
            "resolved_calls": resolved,
            "escalated_calls": escalated,
            "total_faqs": total_faqs,
            "total_products": total_products,
        }
    return await asyncio.to_thread(query)


@app.get("/api/admin/users")
async def list_all_users(_admin: dict = Depends(require_admin)):
    """List all registered users with activity stats and primary admin protection flag."""
    def query():
        with db() as conn:
            rows = conn.execute(
                """
                SELECT u.id, u.email, u.phone, u.full_name, u.role, u.is_active, u.created_at, u.last_login,
                       (SELECT COUNT(*) FROM calls c WHERE c.user_id = u.id) AS call_count,
                       (SELECT COUNT(*) FROM faqs f WHERE f.user_id = u.id) AS faq_count,
                       (SELECT COUNT(*) FROM products p WHERE p.user_id = u.id) AS product_count
                FROM users u
                ORDER BY u.id ASC
                """
            ).fetchall()
            user_list = []
            for r in rows:
                d = dict(r)
                d["is_primary"] = is_primary_admin(user_id=d["id"], email=d["email"])
                user_list.append(d)
            return user_list
    return await asyncio.to_thread(query)


@app.post("/api/admin/users", status_code=201)
async def create_user_by_admin(req: AdminUserCreate, _admin: dict = Depends(require_admin)):
    """Create a new user or administrator directly from the SaaS administration panel."""
    clean_email = req.email.strip().lower()
    clean_phone = re.sub(r"[\s\-()]", "", req.phone.strip())

    if not re.search(r"[A-Za-z]", req.password) or not re.search(r"[0-9]", req.password):
        raise HTTPException(
            status_code=400,
            detail="Password must be at least 8 characters and contain both letters and numbers.",
        )

    def query():
        with db() as conn:
            if conn.execute("SELECT 1 FROM users WHERE LOWER(email) = ?", (clean_email,)).fetchone():
                return 409, "An account with this email address already exists."
            if conn.execute("SELECT 1 FROM users WHERE phone = ?", (clean_phone,)).fetchone():
                return 409, "An account with this phone number already exists."

            password_hash = hash_password(req.password)
            cursor = conn.execute(
                "INSERT INTO users (email, phone, full_name, password_hash, role, is_active, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (clean_email, clean_phone, req.full_name.strip(), password_hash, req.role, 1 if req.is_active else 0, now()),
            )
            new_id = cursor.lastrowid
            row = conn.execute(
                "SELECT id, email, phone, full_name, role, is_active, created_at, last_login FROM users WHERE id = ?",
                (new_id,),
            ).fetchone()
            d = dict(row)
            d["is_primary"] = is_primary_admin(user_id=d["id"], email=d["email"])
            return 201, d

    status_code, result = await asyncio.to_thread(query)
    if status_code == 409:
        raise HTTPException(status_code=409, detail=result)
    return result


@app.put("/api/admin/users/{user_id}")
async def update_user_by_admin(user_id: int, req: AdminUserUpdate, admin: dict = Depends(require_admin)):
    """Update user details (name, email, phone, role, status, optional password)."""
    clean_email = req.email.strip().lower()
    clean_phone = re.sub(r"[\s\-()]", "", req.phone.strip())

    if is_primary_admin(user_id=user_id):
        # Primary Admin cannot be modified by secondary admins, and cannot be demoted or suspended
        if admin["id"] != user_id or req.role != "admin" or not req.is_active:
            raise HTTPException(
                status_code=403,
                detail="Primary Platform Administrator cannot be modified, demoted, suspended, or deleted.",
            )

    if req.password:
        if len(req.password) < 8 or not re.search(r"[A-Za-z]", req.password) or not re.search(r"[0-9]", req.password):
            raise HTTPException(
                status_code=400,
                detail="New password must be at least 8 characters and contain both letters and numbers.",
            )

    def query():
        with db() as conn:
            existing = conn.execute("SELECT id, email, phone, role, is_active FROM users WHERE id = ?", (user_id,)).fetchone()
            if not existing:
                return 404, "User not found."

            # Check email uniqueness
            email_conflict = conn.execute("SELECT 1 FROM users WHERE LOWER(email) = ? AND id != ?", (clean_email, user_id)).fetchone()
            if email_conflict:
                return 409, "An account with this email address already exists."

            # Check phone uniqueness
            phone_conflict = conn.execute("SELECT 1 FROM users WHERE phone = ? AND id != ?", (clean_phone, user_id)).fetchone()
            if phone_conflict:
                return 409, "An account with this phone number already exists."

            if req.password:
                new_hash = hash_password(req.password)
                conn.execute(
                    "UPDATE users SET email = ?, phone = ?, full_name = ?, password_hash = ?, role = ?, is_active = ? WHERE id = ?",
                    (clean_email, clean_phone, req.full_name.strip(), new_hash, req.role, 1 if req.is_active else 0, user_id),
                )
            else:
                conn.execute(
                    "UPDATE users SET email = ?, phone = ?, full_name = ?, role = ?, is_active = ? WHERE id = ?",
                    (clean_email, clean_phone, req.full_name.strip(), req.role, 1 if req.is_active else 0, user_id),
                )

            row = conn.execute(
                "SELECT id, email, phone, full_name, role, is_active, created_at, last_login FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
            d = dict(row)
            d["is_primary"] = is_primary_admin(user_id=d["id"], email=d["email"])
            return 200, d

    status_code, result = await asyncio.to_thread(query)
    if status_code == 404:
        raise HTTPException(status_code=404, detail=result)
    if status_code == 409:
        raise HTTPException(status_code=409, detail=result)
    return result


@app.put("/api/admin/users/{user_id}/status")
async def update_user_status(user_id: int, req: UserStatusUpdate, admin: dict = Depends(require_admin)):
    """Activate or suspend a user account with Primary Admin immunity."""
    if is_primary_admin(user_id=user_id):
        raise HTTPException(
            status_code=403,
            detail="Primary Platform Administrator cannot be modified, demoted, suspended, or deleted.",
        )
    if user_id == admin["id"] and not req.is_active:
        raise HTTPException(status_code=400, detail="Administrators cannot suspend their own active account.")

    def query():
        with db() as conn:
            user = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
            if not user:
                return False
            conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if req.is_active else 0, user_id))
            return True

    updated = await asyncio.to_thread(query)
    if not updated:
        raise HTTPException(status_code=404, detail="User not found.")
    return {"status": "ok", "message": f"User account has been {'activated' if req.is_active else 'suspended'}."}


@app.put("/api/admin/users/{user_id}/role")
async def update_user_role(user_id: int, req: UserRoleUpdate, admin: dict = Depends(require_admin)):
    """Promote or demote a user's role (admin or user) with Primary Admin immunity."""
    if is_primary_admin(user_id=user_id):
        raise HTTPException(
            status_code=403,
            detail="Primary Platform Administrator cannot be modified, demoted, suspended, or deleted.",
        )
    if user_id == admin["id"] and req.role != "admin":
        raise HTTPException(status_code=400, detail="Administrators cannot demote themselves.")

    def query():
        with db() as conn:
            user = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
            if not user:
                return False
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (req.role, user_id))
            return True

    updated = await asyncio.to_thread(query)
    if not updated:
        raise HTTPException(status_code=404, detail="User not found.")
    return {"status": "ok", "message": f"User role updated to '{req.role}'."}


@app.delete("/api/admin/users/{user_id}")
async def delete_user_by_admin(user_id: int, admin: dict = Depends(require_admin)):
    """Permanently delete a user account and associated call history with Primary Admin immunity."""
    if is_primary_admin(user_id=user_id):
        raise HTTPException(
            status_code=403,
            detail="Primary Platform Administrator cannot be modified, demoted, suspended, or deleted.",
        )
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Administrators cannot delete their own active session account.")

    def query():
        with db() as conn:
            user = conn.execute("SELECT id, email, full_name FROM users WHERE id = ?", (user_id,)).fetchone()
            if not user:
                return False
            # Clean up calls and call_messages
            user_calls = conn.execute("SELECT id FROM calls WHERE user_id = ?", (user_id,)).fetchall()
            for c in user_calls:
                conn.execute("DELETE FROM call_messages WHERE call_id = ?", (c["id"],))
            conn.execute("DELETE FROM calls WHERE user_id = ?", (user_id,))
            # Reassign faqs and products to null/platform so catalog integrity is maintained
            conn.execute("UPDATE faqs SET user_id = NULL WHERE user_id = ?", (user_id,))
            conn.execute("UPDATE products SET user_id = NULL WHERE user_id = ?", (user_id,))
            # Delete user
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            return True

    deleted = await asyncio.to_thread(query)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found.")
    return {"status": "ok", "message": "User account and associated records deleted successfully."}


# =====================================================================
# 13. System Health & Key Status
# =====================================================================
@app.get("/api/health")
async def health_check(request: Request):
    """Return system health, current engine, and tenant-scoped counts."""
    user = await get_optional_user(request)

    def query():
        with db() as conn:
            if user and user["role"] == "admin":
                faq_count = conn.execute("SELECT COUNT(*) AS n FROM faqs").fetchone()["n"]
                product_count = conn.execute("SELECT COUNT(*) AS n FROM products").fetchone()["n"]
            elif user:
                faq_count = conn.execute("SELECT COUNT(*) AS n FROM faqs WHERE user_id IS NULL OR user_id = ?", (user["id"],)).fetchone()["n"]
                product_count = conn.execute("SELECT COUNT(*) AS n FROM products WHERE user_id IS NULL OR user_id = ?", (user["id"],)).fetchone()["n"]
            else:
                faq_count = conn.execute("SELECT COUNT(*) AS n FROM faqs WHERE user_id IS NULL").fetchone()["n"]
                product_count = conn.execute("SELECT COUNT(*) AS n FROM products WHERE user_id IS NULL").fetchone()["n"]
        return faq_count, product_count

    faqs, products = await asyncio.to_thread(query)
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "engine": "google-genai" if NEW_GENAI_AVAILABLE else "google-generativeai",
        "faqs": faqs,
        "products": products,
        "user": user["full_name"] if user else "Guest",
        "role": user["role"] if user else "guest",
    }


@app.get("/api/key-status")
async def get_key_status(admin: dict = Depends(require_admin)):
    """Return current API key status for monitoring (Admin Only)."""
    return key_manager.status_dict()


# =====================================================================
# 14. Voice Agent & Chat Endpoints
# =====================================================================
@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest, request: Request):
    """Process a user message with Gemini AI and log to user account if authenticated."""
    text = req.message.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Message is empty.")

    opt_user = await get_optional_user(request)
    user_id = opt_user["id"] if opt_user else None

    result = await ask_gemini(req.session_id, text, user_id)
    remember(req.session_id, text, result["reply"])

    try:
        await asyncio.to_thread(save_message, req.session_id, text, result, user_id)
    except Exception as exc:
        log.error("Failed to run save_message: %r", exc)

    return result


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


# =====================================================================
# 15. FAQ Management Endpoints (Multi-Tenant Scoped)
# =====================================================================
@app.get("/api/faqs")
async def get_faq_chips(request: Request, limit: int = Query(default=18, ge=1, le=50)):
    """Return top suggested questions as quick-click slider chips."""
    user = await get_optional_user(request)

    def query():
        with db() as conn:
            if user and user["role"] == "admin":
                rows = conn.execute("SELECT question, intent FROM faqs ORDER BY id ASC LIMIT ?", (limit,)).fetchall()
            elif user:
                rows = conn.execute(
                    """
                    SELECT question, intent FROM faqs
                    WHERE user_id IS NULL OR user_id IN (SELECT id FROM users WHERE role = 'admin') OR user_id = ?
                    ORDER BY id ASC LIMIT ?
                    """,
                    (user["id"], limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT question, intent FROM faqs
                    WHERE user_id IS NULL OR user_id IN (SELECT id FROM users WHERE role = 'admin')
                    ORDER BY id ASC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            return [{"question": r["question"], "intent": r["intent"]} for r in rows]

    return {"questions": await asyncio.to_thread(query)}


@app.get("/api/faqs/all")
async def get_all_faqs(request: Request):
    """Return FAQs: regular users see Platform/Admin + their own; Admins see all."""
    user = await get_optional_user(request)

    def query():
        with db() as conn:
            if user and user["role"] == "admin":
                rows = conn.execute(
                    """
                    SELECT f.id, f.intent, f.question, f.answer, f.user_id,
                           COALESCE(u.full_name, 'Platform') AS owner_name, u.email AS owner_email
                    FROM faqs f
                    LEFT JOIN users u ON f.user_id = u.id
                    ORDER BY f.id ASC
                    """
                ).fetchall()
            elif user:
                rows = conn.execute(
                    """
                    SELECT f.id, f.intent, f.question, f.answer, f.user_id,
                           CASE WHEN f.user_id IS NULL THEN 'Platform'
                                WHEN u.role = 'admin' THEN 'Platform (Admin)'
                                WHEN f.user_id = ? THEN 'You'
                                ELSE 'Custom' END AS owner_name
                    FROM faqs f
                    LEFT JOIN users u ON f.user_id = u.id
                    WHERE f.user_id IS NULL OR f.user_id IN (SELECT id FROM users WHERE role = 'admin') OR f.user_id = ?
                    ORDER BY f.id ASC
                    """,
                    (user["id"], user["id"]),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT f.id, f.intent, f.question, f.answer, f.user_id, 'Platform' AS owner_name
                    FROM faqs f
                    LEFT JOIN users u ON f.user_id = u.id
                    WHERE f.user_id IS NULL OR f.user_id IN (SELECT id FROM users WHERE role = 'admin')
                    ORDER BY f.id ASC
                    """
                ).fetchall()
            return [dict(r) for r in rows]

    return await asyncio.to_thread(query)


@app.post("/api/faqs", status_code=201)
async def create_faq(faq: FAQCreate, current_user: dict = Depends(get_current_user)):
    """Add a new FAQ entry owned by current user (or platform if admin)."""
    user_id = current_user["id"]

    def query():
        with db() as conn:
            # Sync sequence to current max(id) so new insertion gets max(id) + 1
            max_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM faqs").fetchone()[0]
            conn.execute("DELETE FROM sqlite_sequence WHERE name = 'faqs'")
            conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('faqs', ?)", (max_id,))
            cursor = conn.execute(
                "INSERT INTO faqs (intent, question, answer, user_id) VALUES (?, ?, ?, ?)",
                (faq.intent.strip(), faq.question.strip(), faq.answer.strip(), user_id),
            )
            row = conn.execute(
                "SELECT id, intent, question, answer, user_id FROM faqs WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            return dict(row)

    res = await asyncio.to_thread(query)
    try:
        refresh_knowledge_base()
    except Exception as exc:
        log.warning("Notice refreshing knowledge base after FAQ create: %r", exc)
    return res


@app.put("/api/faqs/{faq_id}")
async def update_faq(faq_id: int, faq: FAQUpdate, current_user: dict = Depends(get_current_user)):
    """Update FAQ entry: users can only update their own; Admins can update any."""
    def query():
        with db() as conn:
            existing = conn.execute("SELECT id, user_id FROM faqs WHERE id = ?", (faq_id,)).fetchone()
            if not existing:
                return 404
            if current_user["role"] != "admin" and existing["user_id"] != current_user["id"]:
                return 403

            conn.execute(
                "UPDATE faqs SET intent = ?, question = ?, answer = ? WHERE id = ?",
                (faq.intent.strip(), faq.question.strip(), faq.answer.strip(), faq_id),
            )
            row = conn.execute(
                "SELECT id, intent, question, answer, user_id FROM faqs WHERE id = ?", (faq_id,)
            ).fetchone()
            return dict(row)

    result = await asyncio.to_thread(query)
    if result == 404:
        raise HTTPException(status_code=404, detail="FAQ not found.")
    if result == 403:
        raise HTTPException(status_code=403, detail="You do not have permission to modify this FAQ.")

    try:
        refresh_knowledge_base()
    except Exception as exc:
        log.warning("Notice refreshing knowledge base after FAQ update: %r", exc)
    return result


@app.delete("/api/faqs/{faq_id}", status_code=204)
async def delete_faq(faq_id: int, current_user: dict = Depends(get_current_user)):
    """Delete FAQ entry and re-sequence database IDs so sequence remains contiguous."""
    def query():
        with db() as conn:
            existing = conn.execute("SELECT id, user_id FROM faqs WHERE id = ?", (int(faq_id),)).fetchone()
            if not existing:
                return 404
            if current_user["role"] != "admin" and existing["user_id"] != current_user["id"]:
                return 403
            conn.execute("DELETE FROM faqs WHERE id = ?", (int(faq_id),))
            resequence_table(conn, "faqs")
            return 204

    code = await asyncio.to_thread(query)
    if code == 404:
        raise HTTPException(status_code=404, detail="FAQ not found.")
    if code == 403:
        raise HTTPException(status_code=403, detail="You do not have permission to delete this FAQ.")

    try:
        refresh_knowledge_base()
    except Exception as exc:
        log.warning("Notice refreshing knowledge base after FAQ delete: %r", exc)

    return Response(status_code=204)


# =====================================================================
# 16. Product Catalog Endpoints (Multi-Tenant Scoped)
# =====================================================================
@app.get("/api/products")
async def get_products(request: Request, category: str | None = Query(default=None)):
    """Return products: Users see Platform/Admin + their own; Admins see all."""
    user = await get_optional_user(request)

    def query():
        with db() as conn:
            params = []
            where_clauses = []

            if not user:
                where_clauses.append("(p.user_id IS NULL OR p.user_id IN (SELECT id FROM users WHERE role = 'admin'))")
            elif user["role"] != "admin":
                where_clauses.append("(p.user_id IS NULL OR p.user_id IN (SELECT id FROM users WHERE role = 'admin') OR p.user_id = ?)")
                params.append(user["id"])

            if category and category.strip():
                where_clauses.append("LOWER(p.category) = LOWER(?)")
                params.append(category.strip())

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            query_sql = f"""
                SELECT p.id, p.name, p.category, p.brand, p.price, p.stock_status, p.description, p.user_id,
                       COALESCE(u.full_name, 'Platform') AS owner_name
                FROM products p
                LEFT JOIN users u ON p.user_id = u.id
                {where_sql}
                ORDER BY p.id ASC
            """
            rows = conn.execute(query_sql, params).fetchall()
            return [dict(r) for r in rows]

    return await asyncio.to_thread(query)


@app.post("/api/products", status_code=201)
async def create_product(product: ProductCreate, current_user: dict = Depends(get_current_user)):
    """Add a new product entry owned by current user (or platform if admin)."""
    user_id = current_user["id"]

    def query():
        with db() as conn:
            # Sync sequence to current max(id) so new insertion gets max(id) + 1
            max_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM products").fetchone()[0]
            conn.execute("DELETE FROM sqlite_sequence WHERE name = 'products'")
            conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('products', ?)", (max_id,))
            cursor = conn.execute(
                "INSERT INTO products (name, category, brand, price, stock_status, description, user_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    product.name.strip(),
                    product.category.strip(),
                    product.brand.strip(),
                    product.price,
                    product.stock_status.strip(),
                    product.description.strip(),
                    user_id,
                ),
            )
            row = conn.execute(
                "SELECT id, name, category, brand, price, stock_status, description, user_id FROM products WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            return dict(row)

    res = await asyncio.to_thread(query)
    try:
        refresh_knowledge_base()
    except Exception as exc:
        log.warning("Notice refreshing knowledge base after product create: %r", exc)
    return res


@app.put("/api/products/{product_id}")
async def update_product(product_id: int, product: ProductUpdate, current_user: dict = Depends(get_current_user)):
    """Update product: users can only update their own; Admins can update any."""
    def query():
        with db() as conn:
            existing = conn.execute("SELECT id, user_id FROM products WHERE id = ?", (int(product_id),)).fetchone()
            if not existing:
                return 404
            if current_user["role"] != "admin" and existing["user_id"] != current_user["id"]:
                return 403

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
                "SELECT id, name, category, brand, price, stock_status, description, user_id FROM products WHERE id = ?",
                (int(product_id),),
            ).fetchone()
            return dict(row)

    result = await asyncio.to_thread(query)
    if result == 404:
        raise HTTPException(status_code=404, detail="Product not found.")
    if result == 403:
        raise HTTPException(status_code=403, detail="You do not have permission to modify this product.")

    try:
        refresh_knowledge_base()
    except Exception as exc:
        log.warning("Notice refreshing knowledge base after product update: %r", exc)
    return result


@app.delete("/api/products/{product_id}", status_code=204)
async def delete_product(product_id: int, current_user: dict = Depends(get_current_user)):
    """Delete product and re-sequence database IDs so sequence remains contiguous."""
    def query():
        with db() as conn:
            existing = conn.execute("SELECT id, user_id FROM products WHERE id = ?", (int(product_id),)).fetchone()
            if not existing:
                return 404
            if current_user["role"] != "admin" and existing["user_id"] != current_user["id"]:
                return 403
            conn.execute("DELETE FROM products WHERE id = ?", (int(product_id),))
            resequence_table(conn, "products")
            return 204

    code = await asyncio.to_thread(query)
    if code == 404:
        raise HTTPException(status_code=404, detail="Product not found.")
    if code == 403:
        raise HTTPException(status_code=403, detail="You do not have permission to delete this product.")

    try:
        refresh_knowledge_base()
    except Exception as exc:
        log.warning("Notice refreshing knowledge base after product delete: %r", exc)
    return Response(status_code=204)


# =====================================================================
# 17. Call Log Endpoints (Multi-Tenant Scoped — OWASP Top 10 A01)
# =====================================================================
@app.get("/api/calls")
async def get_recent_calls(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    target_user_id: int | None = Query(default=None),
):
    """Return recent calls: Regular users strictly see their own; Admins can see all."""
    user = await get_optional_user(request)

    def query():
        with db() as conn:
            if user and user["role"] == "admin":
                if target_user_id:
                    rows = conn.execute(
                        """
                        SELECT c.session_id, c.started_at, c.last_activity, c.status, c.outcome, c.total_messages,
                               u.full_name AS caller_name, u.email AS caller_email
                        FROM calls c
                        LEFT JOIN users u ON c.user_id = u.id
                        WHERE c.user_id = ?
                        ORDER BY c.id DESC LIMIT ?
                        """,
                        (target_user_id, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT c.session_id, c.started_at, c.last_activity, c.status, c.outcome, c.total_messages,
                               u.full_name AS caller_name, u.email AS caller_email
                        FROM calls c
                        LEFT JOIN users u ON c.user_id = u.id
                        ORDER BY c.id DESC LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
            elif user:
                # Regular authenticated user — strictly isolated to their own user_id
                rows = conn.execute(
                    """
                    SELECT session_id, started_at, last_activity, status, outcome, total_messages
                    FROM calls
                    WHERE user_id = ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (user["id"], limit),
                ).fetchall()
            else:
                # Unauthenticated guest — return empty or recent guest calls without leak
                rows = conn.execute(
                    "SELECT session_id, started_at, last_activity, status, outcome, total_messages FROM calls WHERE user_id IS NULL ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()

            return [dict(r) for r in rows]

    return await asyncio.to_thread(query)


@app.get("/api/history")
async def get_conversation_history(
    request: Request,
    session_id: str = Query(..., pattern=SESSION_ID_PATTERN),
):
    """Return full conversation transcript for a session, checking tenant ownership."""
    user = await get_optional_user(request)

    def query():
        with db() as conn:
            call = conn.execute("SELECT id, user_id FROM calls WHERE session_id = ?", (session_id,)).fetchone()
            if not call:
                return 404, []

            # OWASP A01: Prevent IDOR (Insecure Direct Object Reference)
            if call["user_id"] is not None:
                if not user or (user["role"] != "admin" and user["id"] != call["user_id"]):
                    return 403, []

            rows = conn.execute(
                "SELECT id, timestamp, user_transcript, ai_response, detected_intent, outcome FROM call_messages WHERE call_id = ? ORDER BY id ASC",
                (call["id"],),
            ).fetchall()
            return 200, [dict(r) for r in rows]

    status_code, data = await asyncio.to_thread(query)
    if status_code == 404:
        raise HTTPException(status_code=404, detail="Call session not found.")
    if status_code == 403:
        raise HTTPException(status_code=403, detail="Access denied. You do not own this call transcript.")

    return data


@app.post("/api/calls/sync")
async def sync_client_calls(req: SyncCallRequest, request: Request):
    """Sync client-side recorded session logs into SQLite database with user binding."""
    user = await get_optional_user(request)
    user_id = user["id"] if user else None

    def _sync():
        with db() as conn:
            # OWASP A01: Prevent IDOR - Verify session ownership if session exists
            existing = conn.execute("SELECT id, user_id FROM calls WHERE session_id = ?", (req.session_id,)).fetchone()
            if existing and existing["user_id"] is not None:
                if user_id != existing["user_id"] and (not user or user.get("role") != "admin"):
                    return {"status": "error", "detail": "Access denied. Cannot sync to another user's session."}
            call_id = get_or_create_call(conn, req.session_id, user_id)
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


# =====================================================================
# 18. Main Server Entrypoint
# =====================================================================
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
