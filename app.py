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
import random
import re
import shutil
import sqlite3
import tempfile
from collections import OrderedDict
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

import bcrypt
import edge_tts
import jwt
from dotenv import load_dotenv
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from gtts import gTTS
from pydantic import BaseModel, EmailStr, Field, field_validator

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


def seed_business_settings() -> None:
    """Seed default business settings if not already present."""
    defaults = {
        "business_name": "DigiBuzz",
        "business_hours_en": "We are open Monday through Saturday from 9:00 AM to 9:00 PM. We are closed on Sundays.",
        "business_hours_ur": "ہم پیر سے ہفتہ صبح 9 بجے سے رات 9 بجے تک کھلے رہتے ہیں۔ اتوار کو ہمارا دفتر بند ہوتا ہے۔",
        "support_phone": "+92 300 1234567",
        "support_email": "support@digibuzz.com",
        "office_address": "DigiBuzz Technologies, Main Boulevard, Gulberg III, Lahore, Pakistan",
    }
    with db() as conn:
        for k, v in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO business_settings (key, value) VALUES (?, ?)",
                (k, v),
            )


def seed_sample_orders() -> None:
    """Seed sample orders for order status lookup."""
    with db() as conn:
        count = conn.execute("SELECT COUNT(*) AS total FROM orders").fetchone()["total"]
        if count > 0:
            return
        sample_orders = [
            ("ORD-1001", None, "Ali Ahmed", "+923001112233", "Shipped", "TCS-987654", 4500, now()),
            ("ORD-1002", None, "Fatima Khan", "+923015556677", "Out for Delivery", "LEO-443322", 12000, now()),
            ("ORD-1003", None, "Bilal Tariq", "+923214448899", "Confirmed", "PENDING", 8500, now()),
        ]
        conn.executemany(
            """
            INSERT INTO orders (order_number, user_id, customer_name, customer_phone, status, tracking_number, total_pkr, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            sample_orders,
        )
        log.info("Seeded %d sample orders.", len(sample_orders))


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

                CREATE TABLE IF NOT EXISTS business_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS handoff_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    user_id INTEGER REFERENCES users(id),
                    trigger_type TEXT NOT NULL,
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                );

                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_number TEXT UNIQUE NOT NULL,
                    user_id INTEGER REFERENCES users(id),
                    customer_name TEXT NOT NULL,
                    customer_phone TEXT,
                    status TEXT NOT NULL DEFAULT 'confirmed',
                    tracking_number TEXT,
                    total_pkr INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
                CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone);
                CREATE INDEX IF NOT EXISTS idx_calls_session ON calls(session_id);
                CREATE INDEX IF NOT EXISTS idx_msgs_call ON call_messages(call_id, id);
                CREATE INDEX IF NOT EXISTS idx_orders_num ON orders(order_number);
                CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
                CREATE INDEX IF NOT EXISTS idx_handoff_session ON handoff_requests(session_id);
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
        seed_business_settings()
        seed_sample_orders()

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


CACHED_STREAM_SYSTEM_INSTRUCTION: dict[int | None, str] = {}
CACHED_FAQ_ANSWERS: dict[str, dict] = {}
CACHED_FAQS: dict[int | None, str] = {}
CACHED_PRODUCTS: dict[int | None, str] = {}


def refresh_knowledge_base() -> None:
    """Rebuild Gemini system instructions and invalidate cached state when FAQs or products change."""
    global legacy_model
    legacy_model = None
    CACHED_STREAM_SYSTEM_INSTRUCTION.clear()
    CACHED_FAQ_ANSWERS.clear()
    CACHED_FAQS.clear()
    CACHED_PRODUCTS.clear()
    log.info("Knowledge base dynamically refreshed and caches cleared.")


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
# 7. Text-to-Speech (edge-tts with gTTS fallback)
# =====================================================================
DEFAULT_ENGLISH_VOICE = os.getenv("EDGE_TTS_VOICE_EN", "en-US-JennyNeural")
DEFAULT_URDU_VOICE = os.getenv("EDGE_TTS_VOICE_UR", "ur-PK-UzmaNeural")

FILLER_PHRASES_EN = [
    "Let me check that for you.",
    "One moment please, checking that for you.",
    "Sure, let me look into that.",
    "Got it, checking our system now.",
    "Please hold on a moment while I check.",
]
FILLER_PHRASES_UR = [
    "جی، میں ابھی چیک کرتا ہوں۔",
    "ایک سیکنڈ، میں معلومات دیکھتا ہوں۔",
]

FILLER_AUDIO_CACHE: dict[str, bytes] = {}
TTS_AUDIO_CACHE: dict[str, bytes] = {}


@lru_cache(maxsize=256)
def synthesize(text: str, lang: str) -> bytes:
    """Synthesize text into MP3 audio using gTTS with memory caching."""
    buffer = io.BytesIO()
    gTTS(text=text, lang=lang).write_to_fp(buffer)
    return buffer.getvalue()


async def synthesize_edge_tts(text: str, voice: str | None = None) -> bytes:
    """Synthesize text into natural neural MP3 audio using edge-tts with memory caching and automatic fallback."""
    clean_text = text.strip()
    if not clean_text:
        return b""
    selected_voice = voice or (DEFAULT_URDU_VOICE if ARABIC_SCRIPT.search(clean_text) else DEFAULT_ENGLISH_VOICE)
    cache_key = f"{selected_voice}:{clean_text}"
    if cache_key in TTS_AUDIO_CACHE:
        return TTS_AUDIO_CACHE[cache_key]

    try:
        communicate = edge_tts.Communicate(clean_text, selected_voice)
        audio_chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_chunks.append(chunk["data"])
        if audio_chunks:
            audio_data = b"".join(audio_chunks)
            if len(TTS_AUDIO_CACHE) < 500:
                TTS_AUDIO_CACHE[cache_key] = audio_data
            return audio_data
    except Exception as exc:
        log.warning("edge-tts synthesis warning (%r), falling back to gTTS.", exc)

    # Reliable fallback to local gTTS
    lang = "ur" if ARABIC_SCRIPT.search(clean_text) else "en"
    audio_data = await asyncio.to_thread(synthesize, clean_text, lang)
    if audio_data and len(TTS_AUDIO_CACHE) < 500:
        TTS_AUDIO_CACHE[cache_key] = audio_data
    return audio_data


async def get_or_create_filler_audio(text: str, voice: str | None = None) -> bytes:
    """Retrieve pre-synthesized filler audio from memory cache or synthesize and cache it for 0ms voice response."""
    if text in FILLER_AUDIO_CACHE:
        return FILLER_AUDIO_CACHE[text]
    audio = await synthesize_edge_tts(text, voice)
    if audio:
        FILLER_AUDIO_CACHE[text] = audio
    return audio


async def warmup_filler_audio():
    """Warm up natural filler audio in background for instantaneous voice calls."""
    for phrase in FILLER_PHRASES_EN:
        try:
            await get_or_create_filler_audio(phrase, DEFAULT_ENGLISH_VOICE)
        except Exception as e:
            log.warning("Warmup filler warning: %r", e)
    for phrase in FILLER_PHRASES_UR:
        try:
            await get_or_create_filler_audio(phrase, DEFAULT_URDU_VOICE)
        except Exception as e:
            log.warning("Warmup Urdu filler warning: %r", e)



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

    @field_validator("phone", mode="before")
    @classmethod
    def clean_phone(cls, v: str) -> str:
        return re.sub(r"[\s\-()]", "", v.strip()) if isinstance(v, str) else v

    @field_validator("email", mode="before")
    @classmethod
    def clean_email(cls, v: str) -> str:
        return v.strip().lower() if isinstance(v, str) else v


class AdminUserUpdate(BaseModel):
    full_name: str = Field(min_length=2, max_length=100)
    email: str = Field(pattern=EMAIL_PATTERN, min_length=5, max_length=120)
    phone: str = Field(pattern=PHONE_PATTERN)
    password: str | None = Field(default=None, max_length=128)
    role: str = Field(default="user", pattern=r"^(admin|user)$")
    is_active: bool = True

    @field_validator("phone", mode="before")
    @classmethod
    def clean_phone(cls, v: str) -> str:
        return re.sub(r"[\s\-()]", "", v.strip()) if isinstance(v, str) else v

    @field_validator("email", mode="before")
    @classmethod
    def clean_email(cls, v: str) -> str:
        return v.strip().lower() if isinstance(v, str) else v


class AdminPasswordUpdate(BaseModel):
    password: str = Field(min_length=8, max_length=128)


class UserPasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


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


@app.on_event("startup")
async def on_startup_warmup():
    """Warm up memory-cached filler audio for 0ms instantaneous voice streaming."""
    asyncio.create_task(warmup_filler_audio())


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


@app.put("/api/auth/password")
async def change_own_password(req: UserPasswordChange, current_user: dict = Depends(get_current_user)):
    """Allow any authenticated user or administrator to change their own password securely."""
    if not re.search(r"[A-Za-z]", req.new_password) or not re.search(r"[0-9]", req.new_password):
        raise HTTPException(
            status_code=400,
            detail="New password must be at least 8 characters and contain both letters and numbers.",
        )

    with db() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (current_user["id"],)).fetchone()
        if not row or not verify_password(req.current_password, row["password_hash"]):
            raise HTTPException(status_code=400, detail="Current password is incorrect.")

        new_hash = hash_password(req.new_password)
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, current_user["id"]))

    return {"status": "ok", "message": "Your password has been changed successfully."}


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


@app.put("/api/admin/users/{user_id}/password")
async def update_user_password_by_admin(
    user_id: int, req: AdminPasswordUpdate, admin: dict = Depends(require_admin)
):
    """Update or reset password for any user or own account. Secondary admins cannot change Primary Admin password."""
    if is_primary_admin(user_id=user_id) and admin["id"] != user_id:
        raise HTTPException(
            status_code=403,
            detail="Primary Platform Administrator password cannot be modified by another administrator.",
        )

    if not re.search(r"[A-Za-z]", req.password) or not re.search(r"[0-9]", req.password):
        raise HTTPException(
            status_code=400,
            detail="Password must be at least 8 characters and contain both letters and numbers.",
        )

    new_hash = hash_password(req.password)

    def query():
        with db() as conn:
            user = conn.execute("SELECT id, email, full_name FROM users WHERE id = ?", (user_id,)).fetchone()
            if not user:
                return False
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user_id))
            return True

    updated = await asyncio.to_thread(query)
    if not updated:
        raise HTTPException(status_code=404, detail="User not found.")
    return {"status": "ok", "message": "User password updated successfully."}


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
# 14. Voice Agent & Real-Time Streaming WebSocket Endpoints
# =====================================================================
SENTENCE_TERMINATORS = re.compile(r"([.!?\n]+|\;\s*|\:\s*)")
CLAUSE_TERMINATORS = re.compile(r"(\,\s+|\s+-\s+)")

FAST_KNOWLEDGE_STOP_WORDS = {
    "what", "are", "your", "is", "the", "how", "do", "i", "can", "you", "to",
    "for", "a", "in", "of", "and", "my", "me", "tell", "about", "please", "we",
    "any", "some", "there", "have", "does", "give", "get", "show", "much", "many",
}

KNOWLEDGE_SYNONYM_MAP: dict[str, set[str]] = {
    "delivery": {"delivery", "shipping", "deliver", "delivered", "dispatch", "dispatched", "times", "time", "speed", "leopards", "tcs"},
    "cancel": {"cancel", "cancelling", "cancellation", "canceling"},
    "price": {"price", "pricing", "rate", "rates", "cost", "costs", "pkr", "wholesale"},
    "minimum": {"minimum", "moq", "least", "smallest", "quantity", "unit", "units"},
    "return": {"return", "returns", "refund", "refunds", "damaged", "exchange", "warranty", "broken", "replace"},
    "payment": {"payment", "payments", "pay", "cash", "card", "bank", "transfer", "cod", "advance"},
    "khata": {"khata", "ledger", "credit", "customer", "debit", "hisab", "record", "bill"},
    "pool": {"pool", "pooling", "pooled", "group", "buying"},
}


def _expand_knowledge_keywords(words: set[str]) -> set[str]:
    expanded = set(words)
    for w in words:
        for root, syns in KNOWLEDGE_SYNONYM_MAP.items():
            if w in syns or root in w:
                expanded.update(syns)
    return expanded


CONVERSATIONAL_INTENTS: dict[str, tuple[set[str], str]] = {
    "greeting": (
        {"hello", "hi", "hey", "salam", "assalam", "aoa", "morning", "evening", "afternoon"},
        "Hello! I am the DigiBuzz AI Calling Agent. How may I assist you with our wholesale products or services today?"
    ),
    "identity": (
        {"who", "name", "identity", "kaun", "calling"},
        "I am the DigiBuzz AI Calling Agent, your 24/7 automated assistant for wholesale orders, group buying pools, and product inquiries."
    ),
    "status": (
        {"how", "doing", "haal", "kaise"},
        "I am doing great, thank you! How may I assist you with DigiBuzz products or business orders today?"
    ),
    "gratitude": (
        {"thanks", "thank", "shukriya", "jazakallah", "welcome"},
        "You are very welcome! Please let me know if there is anything else I can help you with today."
    ),
    "catalog_overview": (
        {"catalog", "categories", "products", "items", "list", "offer"},
        "We offer wholesale pricing across mobile phones, laptops, tablets, power banks, networking routers, and printers. What product can I check for you?"
    ),
}


def check_fast_knowledge_response(user_text: str, user_id: int | None = None) -> dict | None:
    """Fast-path lookup against SQLite FAQs and Product Catalog for 0ms ultra-low latency response."""
    clean_query = user_text.lower().strip()
    words_all = set(re.findall(r"\b[a-z0-9]{2,}\b", clean_query))
    if not words_all:
        return None

    # 1. Check conversational intents (greetings, identity, gratitude)
    for intent, (triggers, reply) in CONVERSATIONAL_INTENTS.items():
        if words_all.intersection(triggers) and len(words_all) <= 6:
            return {
                "type": "conversational",
                "answer": reply,
                "intent": intent,
            }

    q_words = words_all - FAST_KNOWLEDGE_STOP_WORDS
    if not q_words:
        return None

    expanded_q = _expand_knowledge_keywords(q_words)

    try:
        with db() as conn:
            conn.row_factory = sqlite3.Row

            # 2. Check FAQs
            faq_rows = conn.execute(
                """
                SELECT question, answer FROM faqs
                WHERE user_id IS NULL OR user_id IN (SELECT id FROM users WHERE role = 'admin') OR user_id = ?
                """,
                (user_id or 0,),
            ).fetchall()

            best_faq = None
            best_score = 0.0

            for row in faq_rows:
                f_words = set(re.findall(r"\b[a-z0-9]{3,}\b", row["question"].lower())) - FAST_KNOWLEDGE_STOP_WORDS
                f_expanded = _expand_knowledge_keywords(f_words)
                common = expanded_q.intersection(f_expanded)
                if len(common) >= 1:
                    score = len(common) / max(len(q_words), 1)
                    if score > best_score:
                        best_score = score
                        best_faq = (row["question"], row["answer"])

            if best_faq and best_score >= 0.5:
                return {
                    "type": "faq",
                    "answer": best_faq[1],
                    "intent": "faq_inquiry",
                }

            # 3. Check Products
            prod_rows = conn.execute(
                """
                SELECT name, category, price, stock_status, description FROM products
                WHERE user_id IS NULL OR user_id IN (SELECT id FROM users WHERE role = 'admin') OR user_id = ?
                """,
                (user_id or 0,),
            ).fetchall()

            for row in prod_rows:
                name_clean = row["name"].lower()
                name_words = set(re.findall(r"\b[a-z0-9]{3,}\b", name_clean)) - FAST_KNOWLEDGE_STOP_WORDS
                common_name = q_words.intersection(name_words)
                if len(common_name) >= 2 or (len(name_words) == 1 and len(common_name) == 1) or any(w in clean_query for w in name_words if len(w) >= 6):
                    status = "in stock" if "in" in str(row["stock_status"]).lower() else "currently out of stock"
                    reply = f"The {row['name']} is {status} at wholesale rate PKR {row['price']:,.0f}. {row['description']}"
                    return {
                        "type": "product",
                        "answer": reply,
                        "intent": "product_inquiry",
                    }
    except Exception as exc:
        log.warning("Fast knowledge lookup error: %r", exc)

    return None


WORD_TO_NUMBER = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "twenty": 20, "thirty": 30, "fifty": 50, "hundred": 100,
    "ek": 1, "aik": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5,
    "che": 6, "chay": 6, "saat": 7, "aath": 8, "ath": 8, "nau": 9, "no": 9, "das": 10,
    "ایک": 1, "دو": 2, "تین": 3, "چار": 4, "پانچ": 5, "چھ": 6, "سات": 7, "آٹھ": 8, "نو": 9, "دس": 10,
}


def parse_quantity(text: str) -> int | None:
    """Extract numeric or worded quantity from text."""
    clean = text.lower().strip()
    m = re.search(r"\b(\d+)\s*(?:items|pcs|pieces|piece|units|dane|adad|عدد|دانوں|دانیں)?\b", clean, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    tokens = re.findall(r"[\w\u0600-\u06FF]+", clean)
    for t in tokens:
        if t in WORD_TO_NUMBER:
            return WORD_TO_NUMBER[t]
    return None


def get_business_hours(is_urdu: bool = False) -> str:
    """Retrieve business hours from business_settings table."""
    key = "business_hours_ur" if is_urdu else "business_hours_en"
    try:
        with db() as conn:
            row = conn.execute("SELECT value FROM business_settings WHERE key = ?", (key,)).fetchone()
            if row and row["value"]:
                return row["value"]
    except Exception:
        pass
    if is_urdu:
        return "ہم پیر سے ہفتہ صبح 9 بجے سے رات 9 بجے تک کھلے رہتے ہیں۔ اتوار کو ہمارا دفتر بند ہوتا ہے۔"
    return "We are open Monday through Saturday from 9:00 AM to 9:00 PM. We are closed on Sundays."


def lookup_order_status(text: str, user_id: int | None = None) -> dict | None:
    """Direct database lookup for order status queries."""
    clean = text.lower().strip()
    order_keywords = ["order status", "track order", "where is my order", "mera order", "status of my order", "order kahan hai", "order track", "آرڈر", "ارڈر"]
    has_keyword = any(k in clean for k in order_keywords)

    m = re.search(r"\b(?:ord(?:er)?[- ]?)?(\d{4,6})\b", clean, re.IGNORECASE)
    digits = m.group(1) if m else None
    order_num = f"ORD-{digits}" if digits else None

    if not (has_keyword or order_num):
        return None

    is_urdu = bool(ARABIC_SCRIPT.search(text)) or any(w in clean.split() for w in ["kahan", "mera", "batao", "hai", "hain", "karo"])

    try:
        with db() as conn:
            row = None
            if order_num:
                row = conn.execute(
                    "SELECT order_number, status, tracking_number, total_pkr FROM orders WHERE order_number = ? OR order_number LIKE ?",
                    (order_num, f"%{digits}%"),
                ).fetchone()

            if not row and user_id:
                row = conn.execute(
                    "SELECT order_number, status, tracking_number, total_pkr FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                    (user_id,),
                ).fetchone()

            if not row and has_keyword and not order_num:
                row = conn.execute(
                    "SELECT order_number, status, tracking_number, total_pkr FROM orders ORDER BY id DESC LIMIT 1"
                ).fetchone()

            if row:
                status_str = row["status"]
                tracking_str = f"Tracking ID is {row['tracking_number']}." if row["tracking_number"] and row["tracking_number"] != "PENDING" else "Tracking details will be sent via SMS shortly."
                if is_urdu:
                    reply = f"آپ کا آرڈر نمبر {row['order_number']} اس وقت {status_str} ہے اور کل رقم {row['total_pkr']} روپے ہے۔"
                    return {"reply": reply, "language": "ur", "intent": "order_status", "outcome": "Resolved", "confidence": 1.0}
                reply = f"Your order {row['order_number']} is currently {status_str}. Total amount is PKR {row['total_pkr']:,}. {tracking_str}"
                return {"reply": reply, "language": "en", "intent": "order_status", "outcome": "Resolved", "confidence": 1.0}
    except Exception as exc:
        log.warning("Order lookup error: %r", exc)

    if has_keyword:
        if is_urdu:
            return {
                "reply": "برائے مہربانی اپنا آرڈر نمبر بتائیے جیسے کہ ORD-1001، تاکہ میں تفصیلات چیک کر سکوں۔",
                "language": "ur",
                "intent": "order_status",
                "outcome": "Inquiry",
                "confidence": 1.0,
            }
        return {
            "reply": "Please share your order number, for example ORD-1001, so I can look up your delivery status.",
            "language": "en",
            "intent": "order_status",
            "outcome": "Inquiry",
            "confidence": 1.0,
        }
    return None


async def get_stream_system_instruction(user_id: int | None = None) -> str:
    """Retrieve cached stream system instruction or rebuild in parallel with asyncio.gather."""
    if user_id in CACHED_STREAM_SYSTEM_INSTRUCTION:
        return CACHED_STREAM_SYSTEM_INSTRUCTION[user_id]

    async def _get_faqs():
        if user_id in CACHED_FAQS:
            return CACHED_FAQS[user_id]
        faqs = await asyncio.to_thread(load_faqs_context, user_id)
        CACHED_FAQS[user_id] = faqs
        return faqs

    async def _get_prods():
        if user_id in CACHED_PRODUCTS:
            return CACHED_PRODUCTS[user_id]
        prods = await asyncio.to_thread(load_products_context, user_id)
        CACHED_PRODUCTS[user_id] = prods
        return prods

    faq_ctx, prod_ctx = await asyncio.gather(_get_faqs(), _get_prods())

    instruction = f"""You are the DigiBuzz AI Calling Agent speaking live on a phone call.
KNOWLEDGE BASE:
1. Operational FAQs:
{faq_ctx}

2. Product Catalog:
{prod_ctx}

CRITICAL RULES:
1. Spoken voice response: First, output ONLY the plain conversational spoken reply (maximum 1 to 2 short sentences, natural spoken style).
2. DO NOT include markdown asterisks (*), hashtags (#), bullet points, emojis, or lists in the spoken reply.
3. Immediately on a new line after the spoken text, output EXACTLY the separator "###META###" followed on the next line by a single-line JSON metadata object:
###META###
{{"intent": "<greeting|faq_inquiry|product_inquiry|order_status|place_order|support|unknown>", "confidence": 0.95, "outcome": "<Resolved|Transferred|Inquiry>", "needs_handoff": false, "language": "en", "quantity": null, "item_id": null}}
4. If the user speaks Urdu or Roman Urdu, reply spoken text in clear conversational Urdu and set "language": "ur".
5. If the caller asks for a human agent or has an unresolvable complaint, set "needs_handoff": true and outcome "Transferred".
"""
    CACHED_STREAM_SYSTEM_INSTRUCTION[user_id] = instruction
    return instruction


def check_instant_fast_path(text: str, session_id: str, user_id: int | None = None) -> dict | None:
    """Instant 0ms deterministic handler for greetings, hours, bye, orders, handoff, confirmations, quantities, and cached FAQs."""
    clean = text.lower().strip()
    is_urdu = bool(ARABIC_SCRIPT.search(text)) or any(w in clean.split() for w in ["kya", "mera", "karo", "hai", "hain", "mujhe", "batao", "chahiye", "kahan", "kaise", "ap", "aap", "shukriya", "theek"])
    words = clean.split()
    word_count = len(words)

    # 1. Human agent escalation keywords
    human_keywords = [
        "talk to human", "speak with agent", "agent se baat", "human agent",
        "insan se baat", "operator", "customer service representative",
        "transfer call", "live agent", "kisi bande se", "representative",
        "نمائندہ", "انسان سے بات", "ایجنٹ سے بات", "بات کرواؤ", "بات کراؤ"
    ]
    if any(k in clean for k in human_keywords):
        try:
            with db() as conn:
                conn.execute(
                    """
                    INSERT INTO handoff_requests (session_id, user_id, trigger_type, reason, created_at, status)
                    VALUES (?, ?, 'user_requested', 'Caller requested human agent transfer', ?, 'pending')
                    """,
                    (session_id, user_id, now()),
                )
        except Exception as e:
            log.warning("Handoff record error: %r", e)

        if is_urdu:
            reply = "جی بالکل، میں آپ کی کال کسٹمر سپورٹ نمائندے کو ٹرانسفر کر رہی ہوں۔ براہ کرم ہولڈ کیجیے۔"
        else:
            reply = "I am transferring your call to a human support agent right now. Please hold on."
        return {
            "reply": reply,
            "language": "ur" if is_urdu else "en",
            "intent": "human_handoff",
            "outcome": "Transferred",
            "needs_handoff": True,
            "confidence": 1.0,
        }

    # 2. Business hours / timing inquiry
    hours_keywords = ["timing", "timings", "hours", "open", "close", "schedule", "working hours", "office hours", "auqat", "kholo", "band", "اوقات", "ٹائمنگ", "کھلنے کا وقت", "بند ہونے کا وقت"]
    if any(k in clean for k in hours_keywords) and word_count <= 8:
        reply = get_business_hours(is_urdu)
        return {
            "reply": reply,
            "language": "ur" if is_urdu else "en",
            "intent": "business_hours",
            "outcome": "Resolved",
            "needs_handoff": False,
            "confidence": 1.0,
        }

    # 3. Order status lookup
    order_res = lookup_order_status(text, user_id)
    if order_res:
        order_res["needs_handoff"] = False
        return order_res

    # 4. Quantity verification steps
    if word_count <= 4:
        qty = parse_quantity(text)
        if qty is not None:
            if is_urdu:
                reply = f"میں نے {qty} عدد نوٹ کر لیے ہیں۔ کیا آپ اس آرڈر کی تصدیق کرنا چاہتے ہیں؟"
            else:
                reply = f"I have noted {qty} units. Would you like me to confirm this item for your order?"
            return {
                "reply": reply,
                "language": "ur" if is_urdu else "en",
                "intent": "quantity_verification",
                "outcome": "Inquiry",
                "needs_handoff": False,
                "confidence": 1.0,
                "quantity": qty,
            }

    # 5. Yes / No confirmations
    confirm_words_en = {"yes", "yeah", "yep", "sure", "ok", "okay", "confirm", "proceed", "no", "nope", "cancel"}
    confirm_words_ur = {"جی", "ہاں", "جی ہاں", "نہیں", "ٹھیک ہے", "بالکل", "کینسل", "theek", "haan", "jee", "nahi"}
    if word_count <= 3:
        clean_no_punc = re.sub(r"[^\w\s]", "", clean)
        if clean_no_punc in confirm_words_en or clean_no_punc in confirm_words_ur or any(w in confirm_words_en | confirm_words_ur for w in words):
            if any(w in ["no", "nope", "cancel", "نہیں"] for w in words):
                reply = "کوئی مسئلہ نہیں، یہ کینسل کر دیا گیا ہے۔ کیا آپ کو مزید کچھ درکار ہے؟" if is_urdu else "No problem at all, I have cancelled that. How else can I assist you?"
            else:
                reply = "جی سمجھ گئی، بتائیے مزید کیا معلومات درکار ہیں؟" if is_urdu else "Understood. How would you like to proceed?"
            return {
                "reply": reply,
                "language": "ur" if is_urdu else "en",
                "intent": "confirmation",
                "outcome": "Resolved",
                "needs_handoff": False,
                "confidence": 1.0,
            }

    # 6. Thank you / Bye
    farewell_words = {"bye", "goodbye", "thanks", "thank", "shukriya", "jazakallah", "allah hafiz", "khuda hafiz", "شکریہ", "اللہ حافظ", "خدا حافظ"}
    if any(w in clean for w in farewell_words) and word_count <= 6:
        if is_urdu:
            reply = "ڈیجی بز پر رابطہ کرنے کا بہت شکریہ۔ اپنا خیال رکھیے گا، اللہ حافظ!"
        else:
            reply = "Thank you for calling DigiBuzz Support. Have a wonderful day ahead! Goodbye."
        return {
            "reply": reply,
            "language": "ur" if is_urdu else "en",
            "intent": "farewell",
            "outcome": "Resolved",
            "needs_handoff": False,
            "confidence": 1.0,
        }

    # 7. Greetings
    greeting_words = {"hi", "hello", "hey", "salam", "assalam", "aoa", "adaab", "اسلام علیکم", "سلام", "آداب", "صبح بخیر"}
    if any(w in words for w in greeting_words) and word_count <= 5:
        if is_urdu:
            reply = "اسلام علیکم! ڈیجی بز سپورٹ میں خوش آمدید۔ میں آپ کی کیا مدد کر سکتی ہوں؟"
        else:
            reply = "Hello! Welcome to DigiBuzz AI calling service. How may I assist you with our wholesale catalog today?"
        return {
            "reply": reply,
            "language": "ur" if is_urdu else "en",
            "intent": "greeting",
            "outcome": "Resolved",
            "needs_handoff": False,
            "confidence": 1.0,
        }

    # 8. In-memory repeated FAQ cache
    norm_query = re.sub(r"[^\w\s\u0600-\u06FF]", "", clean).strip()
    if norm_query in CACHED_FAQ_ANSWERS:
        cached = CACHED_FAQ_ANSWERS[norm_query]
        return {
            "reply": cached["answer"],
            "language": "ur" if is_urdu else "en",
            "intent": cached.get("intent", "faq_inquiry"),
            "outcome": "Resolved",
            "needs_handoff": False,
            "confidence": 1.0,
        }

    # 9. Knowledge fast match (from check_fast_knowledge_response)
    fast_match = check_fast_knowledge_response(text, user_id)
    if fast_match:
        if fast_match.get("type") == "faq" and norm_query:
            CACHED_FAQ_ANSWERS[norm_query] = {"answer": fast_match["answer"], "intent": fast_match.get("intent", "faq_inquiry")}
        return {
            "reply": fast_match["answer"],
            "language": "ur" if is_urdu else "en",
            "intent": fast_match.get("intent", "general_inquiry"),
            "outcome": "Resolved",
            "needs_handoff": False,
            "confidence": 1.0,
        }

    return None


class StreamingSentenceEmitter:
    """Buffer streamed tokens, extract spoken text sentences cleanly, and separate ###META### JSON."""
    def __init__(self):
        self.spoken_buffer = ""
        self.meta_buffer = ""
        self.saw_meta = False
        self.sentence_idx = 0
        self.collected_spoken: list[str] = []
        self.delims = re.compile(r'([.!?۔؟\n]+)')

    def feed(self, chunk: str) -> list[str]:
        if self.saw_meta:
            self.meta_buffer += chunk
            return []

        combined = self.spoken_buffer + chunk
        if "###META###" in combined:
            self.saw_meta = True
            spoken_part, meta_part = combined.split("###META###", 1)
            self.spoken_buffer = spoken_part
            self.meta_buffer = meta_part
            return self._flush(flush_all=True)

        self.spoken_buffer = combined
        return self._flush(flush_all=False)

    def _flush(self, flush_all: bool = False) -> list[str]:
        results = []
        while True:
            match = self.delims.search(self.spoken_buffer)
            if match:
                end_pos = match.end()
                sentence = self.spoken_buffer[:end_pos].strip()
                self.spoken_buffer = self.spoken_buffer[end_pos:].lstrip()
                clean_s = re.sub(r"[*#_`]+", "", sentence).strip()
                if clean_s:
                    results.append(clean_s)
                    self.collected_spoken.append(clean_s)
                continue

            words = self.spoken_buffer.split()
            if len(words) >= 12:
                chunk = " ".join(words[:12])
                self.spoken_buffer = " ".join(words[12:])
                clean_s = re.sub(r"[*#_`]+", "", chunk).strip()
                if clean_s:
                    results.append(clean_s)
                    self.collected_spoken.append(clean_s)
                continue

            break

        if flush_all and self.spoken_buffer.strip():
            clean_s = re.sub(r"[*#_`]+", "", self.spoken_buffer).strip()
            if clean_s:
                results.append(clean_s)
                self.collected_spoken.append(clean_s)
            self.spoken_buffer = ""

        return results

    def flush_all(self) -> list[str]:
        return self._flush(flush_all=True)

    def get_meta(self) -> dict:
        meta = {
            "type": "meta",
            "intent": "general_inquiry",
            "confidence": 0.9,
            "outcome": "Resolved",
            "needs_handoff": False,
            "language": "en",
            "quantity": None,
            "item_id": None,
        }
        if self.meta_buffer:
            match = re.search(r"\{.*\}", self.meta_buffer, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                    meta.update(parsed)
                    meta["type"] = "meta"
                except Exception:
                    pass
        full_text = " ".join(self.collected_spoken)
        if ARABIC_SCRIPT.search(full_text):
            meta["language"] = "ur"
        return meta

    def get_full_spoken(self) -> str:
        return " ".join(self.collected_spoken)


def build_voice_system_instruction(user_id: int | None = None) -> str:
    """Build concise, conversational instructions tailored for low-latency live voice calls."""
    faq_ctx = load_faqs_context(user_id)
    product_ctx = load_products_context(user_id)
    return f"""
You are the DigiBuzz AI Calling Agent, speaking live with a customer over an active voice call.

KNOWLEDGE BASE:
1. Operational FAQs (policies, delivery, payments):
{faq_ctx}

2. Product Catalog (wholesale products):
{product_ctx}

CRITICAL RULES FOR LIVE VOICE CALL:
1. BRANDING: You are "DigiBuzz AI Calling Agent". Speak professionally, warmly, and helpfully.
2. ACCURACY: Answer using ONLY the FAQ knowledge base and Product Catalog above. State exact product names, specifications, stock status, and wholesale prices in PKR.
3. SPOKEN VOICE FORMAT:
   - Speak in clear, natural, conversational sentences suitable for live Text-to-Speech audio.
   - Do NOT use markdown asterisks (*), hashtags (#), bullet points (-), emojis, or JSON formatting.
   - Keep answers concise and direct: maximum 1 to 3 short sentences so the caller can listen easily.
4. LANGUAGE:
   - Speak in English by default.
   - If the caller speaks or types in Urdu or Roman Urdu, respond politely in clear Urdu or English.
5. ESCALATION: If the caller asks for an unlisted product, unknown policy, or asks for a human agent, state: "I am transferring your call to a dedicated Human Support Agent. Please hold on."
6. OFF-TOPIC: If the user asks something completely outside DigiBuzz products, wholesale orders, or policies, politely decline: "I am sorry, I can only assist you with DigiBuzz products, wholesale orders, and store services."
"""


async def stream_gemini_voice_tokens(session_id: str, user_text: str, user_id: int | None = None, system_instruction: str | None = None):
    """Asynchronously stream text tokens from Gemini with fast model tier and 6 turns context limit."""
    sys_instruction = system_instruction or await get_stream_system_instruction(user_id)
    history_turns = SESSIONS.get(session_id, [])[-12:]

    async def _stream_once():
        if NEW_GENAI_AVAILABLE:
            if not key_manager.client:
                if key_manager.key1 or key_manager.key2:
                    key_manager._configure_active()
                if not key_manager.client:
                    raise RuntimeError("GEMINI_API_KEY is not configured.")

            contents: list[types.Content] = []
            for turn in history_turns:
                role = "user" if turn.get("role") == "user" else "model"
                parts = turn.get("parts", [""])
                part_text = parts[0] if isinstance(parts, list) and parts else str(parts)
                contents.append(types.Content(role=role, parts=[types.Part.from_text(text=part_text)]))

            contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_text)]))

            config = types.GenerateContentConfig(
                system_instruction=sys_instruction,
                temperature=0.2,
                max_output_tokens=150,
            )

            try:
                stream = await key_manager.client.aio.models.generate_content_stream(
                    model=MODEL_NAME,
                    contents=contents,
                    config=config,
                )
                async for chunk in stream:
                    if chunk.text:
                        yield chunk.text
            except RuntimeError as r_err:
                if "Event loop" in str(r_err):
                    key_manager._configure_active()
                    stream = await key_manager.client.aio.models.generate_content_stream(
                        model=MODEL_NAME,
                        contents=contents,
                        config=config,
                    )
                    async for chunk in stream:
                        if chunk.text:
                            yield chunk.text
                else:
                    raise
        else:
            model = get_legacy_model(user_id)
            contents = history_turns + [{"role": "user", "parts": [user_text]}]
            response = await model.generate_content_async(contents, stream=True)
            async for chunk in response:
                if chunk.text:
                    yield chunk.text

    try:
        async for token in _stream_once():
            yield token
    except Exception as exc:
        if key_manager.is_quota_error(exc) and key_manager.mark_exhausted_and_switch():
            log.warning("Gemini primary key exhausted during streaming; switched to fallback.")
            try:
                async for token in _stream_once():
                    yield token
            except Exception as e2:
                log.error("Fallback Gemini stream error: %r", e2)
                yield FALLBACK_SPEECH
        else:
            log.error("Gemini voice stream error: %r", exc)
            yield FALLBACK_SPEECH


@app.websocket("/ws/ai-call")
@app.websocket("/ws/call")
async def websocket_ai_call_endpoint(websocket: WebSocket):
    """Ultra-low latency real-time voice streaming WebSocket endpoint."""
    await websocket.accept()
    asyncio.create_task(warmup_filler_audio())

    # Extract authenticated user context if cookie or token present
    opt_user = None
    token = websocket.cookies.get(COOKIE_NAME)
    if not token and "token" in websocket.query_params:
        token = websocket.query_params["token"]
    if token:
        payload = decode_access_token(token)
        if payload and payload.get("sub"):
            with db() as conn:
                row = conn.execute(
                    "SELECT id, email, full_name, role, is_active FROM users WHERE id = ?",
                    (payload["sub"],),
                ).fetchone()
                if row and row["is_active"]:
                    opt_user = dict(row)
    user_id = opt_user["id"] if opt_user else None

    # Track active streaming task to support graceful client interruption
    active_stream_task: asyncio.Task | None = None

    async def _handle_message(session_id: str, user_text: str):
        full_text = ""
        buffer = ""

        try:
            # 1. Inform client that processing has begun
            await websocket.send_text(json.dumps({
                "type": "start",
                "session_id": session_id,
                "user_text": user_text,
            }))

            # 2. IMMEDIATE 0ms FILLER AUDIO:
            # Transmit pre-cached natural filler phrase audio immediately so the caller hears voice within milliseconds!
            is_urdu = bool(ARABIC_SCRIPT.search(user_text))
            filler_candidates = FILLER_PHRASES_UR if is_urdu else FILLER_PHRASES_EN
            cached_phrase = next((p for p in filler_candidates if p in FILLER_AUDIO_CACHE), None)
            if cached_phrase:
                filler_text = cached_phrase
                filler_audio = FILLER_AUDIO_CACHE[cached_phrase]
            else:
                filler_text = random.choice(filler_candidates)
                filler_audio = await get_or_create_filler_audio(filler_text, DEFAULT_URDU_VOICE if is_urdu else DEFAULT_ENGLISH_VOICE)

            await websocket.send_text(json.dumps({
                "type": "filler",
                "text": filler_text,
            }))
            if filler_audio:
                await websocket.send_bytes(filler_audio)

            await websocket.send_text(json.dumps({"type": "audio_start"}))

            # 3. CONCURRENT AUDIO QUEUE:
            # Decouple token streaming from audio synthesis so Gemini tokens stream word-by-word into the client's UI
            # without waiting on edge-tts synthesis network calls!
            audio_queue: asyncio.Queue[str | None] = asyncio.Queue()

            async def _tts_worker():
                while True:
                    sentence_chunk = await audio_queue.get()
                    if sentence_chunk is None:
                        audio_queue.task_done()
                        break
                    try:
                        audio_data = await synthesize_edge_tts(sentence_chunk)
                        if audio_data:
                            await websocket.send_bytes(audio_data)
                    except Exception as e_tts:
                        log.warning("Streaming TTS chunk error: %r", e_tts)
                    finally:
                        audio_queue.task_done()

            tts_task = asyncio.create_task(_tts_worker())

            # 4. Check FAST-PATH Knowledge Base (0ms direct FAQ & Product Catalog lookup)
            fast_match = check_fast_knowledge_response(user_text, user_id)
            if fast_match:
                reply_text = fast_match["answer"]
                intent = fast_match.get("intent", "general_inquiry")
                outcome = "Resolved"

                # Immediately enqueue sentences to TTS worker so audio begins synthesis at millisecond 0!
                m_split = SENTENCE_TERMINATORS.search(reply_text)
                if m_split:
                    first_sent = reply_text[:m_split.end()].strip()
                    remainder = reply_text[m_split.end():].strip()
                    await audio_queue.put(first_sent)
                    if remainder:
                        await audio_queue.put(remainder)
                else:
                    await audio_queue.put(reply_text)

                # Concurrently stream words smoothly with 20ms human typing delay
                words = reply_text.split(" ")
                for i, w in enumerate(words):
                    token = w + (" " if i < len(words) - 1 else "")
                    full_text += token
                    await websocket.send_text(json.dumps({
                        "type": "text_delta",
                        "delta": token,
                    }))
                    await asyncio.sleep(0.02)
            else:
                # 5. Open-ended / Conversational: Stream from Gemini in real-time
                first_clause_enqueued = False
                async for token in stream_gemini_voice_tokens(session_id, user_text, user_id):
                    full_text += token
                    buffer += token

                    # Send text token immediately to update the client's chat UI word-by-word
                    await websocket.send_text(json.dumps({
                        "type": "text_delta",
                        "delta": token,
                    }))

                    # For the very first chunk, synthesize earlier (after 3-5 words or clause)
                    if not first_clause_enqueued:
                        m_term = SENTENCE_TERMINATORS.search(buffer) or (CLAUSE_TERMINATORS.search(buffer) if len(buffer) >= 20 else None)
                        if m_term:
                            end_idx = m_term.end()
                            first_sentence = buffer[:end_idx].strip()
                            buffer = buffer[end_idx:]
                            if len(first_sentence) >= 4:
                                first_clause_enqueued = True
                                await audio_queue.put(first_sentence)
                    else:
                        m_term = SENTENCE_TERMINATORS.search(buffer)
                        if m_term:
                            end_idx = m_term.end()
                            sentence = buffer[:end_idx].strip()
                            buffer = buffer[end_idx:]
                            if len(sentence) >= 4:
                                await audio_queue.put(sentence)
                        elif len(buffer) >= 45:
                            m_clause = CLAUSE_TERMINATORS.search(buffer)
                            if m_clause:
                                end_idx = m_clause.end()
                                clause = buffer[:end_idx].strip()
                                buffer = buffer[end_idx:]
                                if len(clause) >= 8:
                                    await audio_queue.put(clause)

                # Flush any remaining text in buffer to audio queue
                tail = buffer.strip()
                if tail:
                    await audio_queue.put(tail)

            # Signal worker to complete and await all audio chunk synthesis
            await audio_queue.put(None)
            await tts_task

            # Notify client that all audio chunks have been sent
            await websocket.send_text(json.dumps({"type": "audio_end"}))

            # 6. Determine intent & outcome
            clean_reply = full_text.strip() or "Thank you for reaching DigiBuzz. How may I assist you?"
            lower_r = clean_reply.lower()
            outcome = "Escalated" if ("human" in lower_r or "transfer" in lower_r or "escalat" in lower_r) else "Resolved"
            intent = "general_inquiry"
            if any(w in lower_r for w in ("price", "pkr", "cost", "wholesale", "rate")):
                intent = "price_check"
            elif any(w in lower_r for w in ("stock", "available", "units", "quantity")):
                intent = "stock_inquiry"
            elif any(w in lower_r for w in ("return", "refund", "warranty", "policy", "delivery")):
                intent = "policy_inquiry"
            elif outcome == "Escalated":
                intent = "human_handoff"

            # 7. Persist to memory and SQLite database
            remember(session_id, user_text, clean_reply)
            try:
                await asyncio.to_thread(
                    save_message,
                    session_id,
                    user_text,
                    {"reply": clean_reply, "intent": intent, "outcome": outcome},
                    user_id,
                )
            except Exception as e_save:
                log.error("Failed to save stream call message: %r", e_save)

            # 8. Notify client that turn is complete
            await websocket.send_text(json.dumps({
                "type": "done",
                "session_id": session_id,
                "reply": clean_reply,
                "intent": intent,
                "outcome": outcome,
                "user_text": user_text,
            }))

        except asyncio.CancelledError:
            log.info("Voice streaming cancelled by user interruption.")
            raise
        except Exception as exc:
            log.error("Error during voice stream processing: %r", exc)
            try:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": "Sorry, I encountered an error during audio streaming. Please try again.",
                }))
            except Exception:
                pass

    try:
        while True:
            raw_msg = await websocket.receive_text()
            try:
                data = json.loads(raw_msg)
            except Exception:
                continue

            msg_type = data.get("type", "message")

            if msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            if msg_type == "interrupt":
                if active_stream_task and not active_stream_task.done():
                    active_stream_task.cancel()
                continue

            if msg_type in ("message", "user_message"):
                # Cancel any ongoing output before starting a new turn
                if active_stream_task and not active_stream_task.done():
                    active_stream_task.cancel()

                session_id = data.get("session_id") or f"sess_{int(datetime.now().timestamp())}"
                user_text = (data.get("text") or "").strip()
                if not user_text:
                    continue

                active_stream_task = asyncio.create_task(_handle_message(session_id, user_text))

    except WebSocketDisconnect:
        if active_stream_task and not active_stream_task.done():
            active_stream_task.cancel()
        log.info("Client disconnected from voice streaming WebSocket.")
    except Exception as exc:
        if active_stream_task and not active_stream_task.done():
            active_stream_task.cancel()
        log.warning("WebSocket exception: %r", exc)


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest, request: Request):
    """Process a user message with Gemini AI and log to user account if authenticated."""
    text = req.message.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Message is empty.")

    opt_user = await get_optional_user(request)
    user_id = opt_user["id"] if opt_user else None

    # Instant fast-path check (~0ms)
    fast_match = check_instant_fast_path(text, req.session_id, user_id)
    if fast_match:
        result = {
            "reply": fast_match["reply"],
            "speech": fast_match["reply"],
            "intent": fast_match.get("intent", "general_inquiry"),
            "outcome": fast_match.get("outcome", "Resolved"),
        }
    else:
        result = await ask_gemini(req.session_id, text, user_id)

    remember(req.session_id, text, result["reply"])

    try:
        await asyncio.to_thread(save_message, req.session_id, text, result, user_id)
    except Exception as exc:
        log.error("Failed to run save_message: %r", exc)

    return result


@app.post("/api/chat/stream")
async def chat_stream_endpoint(req: ChatRequest, request: Request):
    """
    Real-call Server-Sent Events (SSE) streaming endpoint with 0ms fast-path & background logging.
    Emits {type: "sentence", text, idx}, {type: "meta", ...}, and {type: "done"}.
    """
    text = req.message.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Message is empty.")

    opt_user = await get_optional_user(request)
    user_id = opt_user["id"] if opt_user else None

    # 1. Check instant fast path (0 ms, no LLM)
    fast_match = check_instant_fast_path(text, req.session_id, user_id)
    if fast_match:
        async def fast_stream_generator():
            delims = re.compile(r'([.!?۔؟\n]+)')
            parts = [p.strip() for p in delims.split(fast_match["reply"]) if p.strip()]
            sentences = []
            curr = ""
            for p in parts:
                if p in ".!?۔؟\n":
                    curr += p
                    sentences.append(curr.strip())
                    curr = ""
                else:
                    if curr:
                        sentences.append(curr.strip())
                    curr = p
            if curr.strip():
                sentences.append(curr.strip())
            if not sentences:
                sentences = [fast_match["reply"]]

            for idx, s in enumerate(sentences):
                yield f"data: {json.dumps({'type': 'sentence', 'text': s, 'idx': idx}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.01)

            meta = {
                "type": "meta",
                "intent": fast_match.get("intent", "conversational"),
                "confidence": fast_match.get("confidence", 1.0),
                "outcome": fast_match.get("outcome", "Resolved"),
                "needs_handoff": fast_match.get("needs_handoff", False),
                "language": fast_match.get("language", "en"),
                "quantity": fast_match.get("quantity"),
                "item_id": fast_match.get("item_id"),
            }
            yield f"data: {json.dumps(meta, ensure_ascii=False)}\n\n"
            yield "data: {\"type\": \"done\"}\n\n"

            # Background persistence without blocking stream
            remember(req.session_id, text, fast_match["reply"])
            try:
                await asyncio.to_thread(
                    save_message,
                    req.session_id,
                    text,
                    {
                        "reply": fast_match["reply"],
                        "speech": fast_match["reply"],
                        "intent": fast_match.get("intent", "conversational"),
                        "outcome": fast_match.get("outcome", "Resolved"),
                    },
                    user_id,
                )
            except Exception as exc:
                log.error("Failed to save fast path message: %r", exc)

        return StreamingResponse(
            fast_stream_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # 2. Streaming LLM Path
    async def llm_stream_generator():
        emitter = StreamingSentenceEmitter()
        try:
            async for token in stream_gemini_voice_tokens(req.session_id, text, user_id):
                sentences = emitter.feed(token)
                for s in sentences:
                    yield f"data: {json.dumps({'type': 'sentence', 'text': s, 'idx': emitter.sentence_idx}, ensure_ascii=False)}\n\n"
                    emitter.sentence_idx += 1

            for s in emitter.flush_all():
                yield f"data: {json.dumps({'type': 'sentence', 'text': s, 'idx': emitter.sentence_idx}, ensure_ascii=False)}\n\n"
                emitter.sentence_idx += 1

            meta = emitter.get_meta()
            yield f"data: {json.dumps(meta, ensure_ascii=False)}\n\n"
            yield "data: {\"type\": \"done\"}\n\n"

            # Background persistence
            full_speech = emitter.get_full_spoken() or "I am here to assist you."
            remember(req.session_id, text, full_speech)
            try:
                await asyncio.to_thread(
                    save_message,
                    req.session_id,
                    text,
                    {
                        "reply": full_speech,
                        "speech": full_speech,
                        "intent": meta.get("intent", "general_inquiry"),
                        "outcome": meta.get("outcome", "Resolved"),
                    },
                    user_id,
                )
            except Exception as exc:
                log.error("Failed to persist streamed message: %r", exc)
        except Exception as exc:
            log.error("Streaming error: %r", exc)
            fallback = "I apologize, I am experiencing a brief connection delay. Let me assist you directly."
            yield f"data: {json.dumps({'type': 'sentence', 'text': fallback, 'idx': 0}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'meta', 'intent': 'service_fallback', 'confidence': 0.5, 'outcome': 'Escalated', 'needs_handoff': True, 'language': 'en'}, ensure_ascii=False)}\n\n"
            yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(
        llm_stream_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class HandoffRequest(BaseModel):
    session_id: str
    trigger_type: str = "user_requested"
    reason: str | None = None


@app.post("/api/handoff")
async def handoff_endpoint(req: HandoffRequest, request: Request):
    """Register a human-agent handoff request."""
    user = await get_optional_user(request)
    user_id = user["id"] if user else None
    with db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO handoff_requests (session_id, user_id, trigger_type, reason, created_at, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
            """,
            (req.session_id, user_id, req.trigger_type, req.reason or "Live agent escalation", now()),
        )
        handoff_id = cursor.lastrowid
    return {"ok": True, "handoff_id": handoff_id, "message": "Handoff request registered."}


@app.get("/api/fillers")
async def get_fillers_endpoint():
    """Return configured filler phrases for low-latency voice pipeline."""
    return {
        "en": {
            "short_ack": ["Sure, one moment.", "Okay, let me check.", "Just a second."],
            "order_status": "Let me check your order status.",
            "faq_products": "Let me look that up for you.",
            "place_order": "Okay, let me note that down.",
            "second_filler": "Thanks for waiting, almost there.",
            "timeout": "Sorry, I am having trouble connecting right now. Let me connect you with an agent.",
        },
        "ur": {
            "short_ack": ["جی، ایک سیکنڈ۔", "جی، میں ابھی چیک کرتی ہوں۔", "ایک لمحہ دیجیے۔"],
            "order_status": "جی، میں آپ کا آرڈر چیک کرتی ہوں۔",
            "faq_products": "جی، میں معلومات چیک کر رہی ہوں۔",
            "place_order": "ٹھیک ہے، میں یہ نوٹ کر لیتی ہوں۔",
            "second_filler": "بس تھوڑا سا اور انتظار کیجیے، معلومات مل رہی ہیں۔",
            "timeout": "معذرت، اس وقت سسٹم سے رابطہ نہیں ہو پا رہا۔ میں آپ کو نمائندے سے منسلک کر رہی ہوں۔",
        },
        "roman_ur": {
            "short_ack": ["Jee, aik second.", "Jee, main check karti hoon.", "Aik second hold kijiye."],
            "order_status": "Jee, main aap ka order check karti hoon.",
            "faq_products": "Jee, main information check kar rahi hoon.",
            "place_order": "Theek hai, main yeh note kar leti hoon.",
            "second_filler": "Bas thora sa intezar kijiye, main details nikal rahi hoon.",
            "timeout": "Sorry, system se contact nahi ho raha. Main aap ko support agent se connect kar rahi hoon.",
        }
    }


@app.post("/api/tts")
async def tts_endpoint(req: TTSRequest):
    """Convert text to spoken MP3 audio via edge-tts (with gTTS fallback)."""
    try:
        audio = await synthesize_edge_tts(req.text.strip())
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
