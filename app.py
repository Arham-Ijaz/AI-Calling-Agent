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
from datetime import datetime, date, timedelta, timezone
import io
import json
import logging
import os
import random
import re
import shutil
import tempfile
import time
from typing import Any
import uuid
from bson import ObjectId
import pymongo
from pymongo import MongoClient, ReturnDocument
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


# =====================================================================
# MongoDB Atlas Database Configuration & Vercel Connection Caching
# =====================================================================
MONGODB_URI = os.getenv(
    "MONGODB_URI",
    "mongodb://sheikh66752_db_user:NEmB6MVobQzWfBL9@ac-zlwkrvq-shard-00-00.ksuapus.mongodb.net:27017,ac-zlwkrvq-shard-00-01.ksuapus.mongodb.net:27017,ac-zlwkrvq-shard-00-02.ksuapus.mongodb.net:27017/?ssl=true&replicaSet=atlas-tgmsyx-shard-0&authSource=admin",
)
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "ai_calling_db")

# The MongoClient MUST be initialized globally (outside the FastAPI route functions)
# so Vercel caches the connection pool across serverless invocations.
mongo_client: MongoClient = MongoClient(MONGODB_URI)
mongo_db = mongo_client[MONGODB_DB_NAME]
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
# 4. Database Setup, PyMongo Operations and Seed Data
# =====================================================================
@contextmanager
def db():
    """Context manager yielding global MongoDB database for backward compatibility."""
    yield mongo_db


def get_next_sequence_value(sequence_name: str) -> int:
    """Atomic counter for auto-incrementing integer IDs in MongoDB."""
    doc = mongo_db["counters"].find_one_and_update(
        {"_id": sequence_name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(doc["seq"])


def serialize_doc(doc: dict | None) -> dict | None:
    """Recursively convert MongoDB native ObjectId and date objects to JSON-serializable types."""
    if doc is None:
        return None
    out = {}
    for k, v in doc.items():
        if isinstance(v, ObjectId):
            out[k] = str(v)
        elif isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        elif isinstance(v, list):
            out[k] = [
                serialize_doc(i) if isinstance(i, dict) else (str(i) if isinstance(i, ObjectId) else i)
                for i in v
            ]
        elif isinstance(v, dict):
            out[k] = serialize_doc(v)
        else:
            out[k] = v
    if "id" not in out and "_id" in out:
        out["id"] = out["_id"]
    return out


def serialize_docs(docs) -> list[dict]:
    """Serialize a list or cursor of MongoDB documents."""
    return [serialize_doc(d) for d in docs if d is not None]


def make_id_query(val: int | str, id_field: str = "id") -> dict:
    """Query builder matching integer id, string id, or native ObjectId on _id."""
    conditions = []
    try:
        conditions.append({id_field: int(val)})
    except (ValueError, TypeError):
        pass
    conditions.append({id_field: str(val)})
    if isinstance(val, str) and ObjectId.is_valid(val):
        conditions.append({"_id": ObjectId(val)})
    if len(conditions) == 1:
        return conditions[0]
    return {"$or": conditions}


def seed_products() -> None:
    """Seed the products collection from products.json if empty."""
    if mongo_db["products"].count_documents({}) > 0:
        return
    if not PRODUCTS_PATH.exists():
        return
    try:
        data = json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))
        items = data.get("products", [])
    except Exception as exc:
        log.error("Failed to parse products.json: %r", exc)
        return
    for p in items:
        pid = get_next_sequence_value("products")
        mongo_db["products"].insert_one({
            "id": pid,
            "name": p.get("name", "Unknown Item"),
            "category": p.get("category", "General"),
            "brand": p.get("brand", "Generic"),
            "price": int(p.get("price", 0)),
            "stock_status": p.get("stock_status", "in_stock"),
            "description": p.get("description", ""),
            "item_type": "product",
            "user_id": None,
        })
    log.info("Seeded %d platform products into MongoDB.", len(items))


def seed_faqs() -> None:
    """Seed the faqs collection from faq.json if empty."""
    if mongo_db["faqs"].count_documents({}) > 0:
        return
    if not FAQ_PATH.exists():
        return
    try:
        data = json.loads(FAQ_PATH.read_text(encoding="utf-8"))
        items = data.get("faqs", [])
    except Exception as exc:
        log.error("Failed to parse faq.json: %r", exc)
        return
    seeded = 0
    for f in items:
        if f.get("question") and f.get("answer"):
            fid = get_next_sequence_value("faqs")
            mongo_db["faqs"].insert_one({
                "id": fid,
                "intent": f.get("intent", "general"),
                "question": f.get("question", ""),
                "answer": f.get("answer", ""),
                "user_id": None,
            })
            seeded += 1
    log.info("Seeded %d platform FAQs into MongoDB.", seeded)


PRIMARY_ADMIN_EMAIL = os.getenv("DEFAULT_ADMIN_EMAIL", "admin@digibuzz.com").strip().lower()


def is_primary_admin(user_id: int | str | None = None, email: str | None = None) -> bool:
    """Check if the given user_id or email corresponds to the Primary Super Admin."""
    if user_id in (1, "1"):
        return True
    if email and email.strip().lower() == PRIMARY_ADMIN_EMAIL:
        return True
    try:
        row = mongo_db["users"].find_one(
            {"$or": [{"email": PRIMARY_ADMIN_EMAIL}, {"role": "admin"}]},
            sort=[("id", 1)],
        )
        if row:
            if user_id is not None and (
                row.get("id") == user_id
                or str(row.get("id")) == str(user_id)
                or str(row.get("_id")) == str(user_id)
            ):
                return True
            if email and row.get("email", "").strip().lower() == email.strip().lower():
                return True
    except Exception:
        pass
    return False


def seed_default_admin() -> None:
    """Ensure at least one admin account exists."""
    admin = mongo_db["users"].find_one({"role": "admin"})
    if not admin:
        admin_email = PRIMARY_ADMIN_EMAIL
        admin_phone = os.getenv("DEFAULT_ADMIN_PHONE", "+923001234567").strip()
        admin_pass = os.getenv("DEFAULT_ADMIN_PASSWORD", "Admin@12345")
        hashed = hash_password(admin_pass)
        uid = get_next_sequence_value("users")
        mongo_db["users"].insert_one({
            "id": uid,
            "email": admin_email,
            "phone": admin_phone,
            "full_name": "Platform Administrator",
            "password_hash": hashed,
            "role": "admin",
            "is_active": 1,
            "created_at": now(),
            "last_login": None,
        })
        log.info("Default Admin account created in MongoDB: %s (Password: %s)", admin_email, admin_pass)


def seed_business_settings() -> None:
    """Seed default business settings if not already present."""
    defaults = {
        "business_name": "Apex Support Services",
        "business_type": "Products & Healthcare/Consulting Services",
        "greeting": "Hello! Welcome to our automated customer assistance. How may I assist you with our catalog and services today?",
        "working_hours": "Monday to Saturday from 9:00 AM to 6:00 PM",
        "language_default": "en",
        "max_order_quantity": "10",
        "escalation_message": "I will connect you directly with a human staff member to assist you further.",
        "business_hours_en": "Monday to Saturday from 9:00 AM to 6:00 PM. Closed on Sundays.",
        "business_hours_ur": "ہم پیر سے ہفتہ صبح 9 بجے سے شام 6 بجے تک کھلے رہتے ہیں۔ اتوار کو ہمارا دفتر بند ہوتا ہے۔",
        "support_phone": "+92 300 1234567",
        "support_email": "support@example.com",
        "office_address": "Main Boulevard, Gulberg III, Lahore, Pakistan",
    }
    for k, v in defaults.items():
        mongo_db["business_settings"].update_one(
            {"key": k},
            {"$setOnInsert": {"key": k, "value": v}},
            upsert=True,
        )


def seed_sample_orders() -> None:
    """Seed sample orders for testing status flows and admin confirmation."""
    if mongo_db["orders"].count_documents({}) > 0:
        return
    sample_orders = [
        {"order_number": "ORD-1001", "user_id": None, "customer_name": "Ali Ahmed", "customer_phone": "+923001112233", "status": "pending_admin_confirmation", "item_name_snapshot": "General Medical Consultation", "unit_price_snapshot": 2500, "quantity": 1, "preferred_datetime": "2026-11-10 10:00", "total_pkr": 2500, "created_at": now(), "updated_at": now(), "is_locked": 0},
        {"order_number": "ORD-1002", "user_id": None, "customer_name": "Fatima Khan", "customer_phone": "+923015556677", "status": "confirmed", "item_name_snapshot": "Logitech MK270 Combo", "unit_price_snapshot": 6500, "quantity": 2, "preferred_datetime": None, "total_pkr": 13000, "created_at": now(), "updated_at": now(), "is_locked": 0},
        {"order_number": "ORD-1003", "user_id": None, "customer_name": "Bilal Tariq", "customer_phone": "+923214448899", "status": "cancel_requested", "item_name_snapshot": "Samsung Galaxy A54", "unit_price_snapshot": 89500, "quantity": 1, "preferred_datetime": None, "total_pkr": 89500, "created_at": now(), "updated_at": now(), "is_locked": 0},
    ]
    for o in sample_orders:
        oid = get_next_sequence_value("orders")
        o["id"] = oid
        mongo_db["orders"].insert_one(o)
        eid = get_next_sequence_value("order_events")
        mongo_db["order_events"].insert_one({
            "id": eid,
            "order_id": oid,
            "actor_user_id": None,
            "actor_role": "system",
            "event_type": "order_created",
            "old_status": None,
            "new_status": o["status"],
            "note": "Initial seeded order",
            "created_at": now(),
        })
    log.info("Seeded %d sample orders with audit events.", len(sample_orders))


def init_db() -> None:
    """Initialize MongoDB Atlas collections, indexes, sequence counters, and seed initial data."""
    try:
        mongo_db["users"].create_index("email", unique=True)
        mongo_db["users"].create_index("phone")
        mongo_db["users"].create_index("id")
        mongo_db["calls"].create_index("session_id", unique=True)
        mongo_db["calls"].create_index("user_id")
        mongo_db["calls"].create_index("id")
        mongo_db["call_messages"].create_index("call_id")
        mongo_db["call_messages"].create_index("id")
        mongo_db["products"].create_index("user_id")
        mongo_db["products"].create_index("id")
        mongo_db["faqs"].create_index("user_id")
        mongo_db["faqs"].create_index("id")
        mongo_db["business_settings"].create_index("key", unique=True)
        mongo_db["handoff_requests"].create_index("session_id")
        mongo_db["handoff_requests"].create_index("status")
        mongo_db["handoff_requests"].create_index("priority")
        mongo_db["handoff_requests"].create_index("id")
        mongo_db["orders"].create_index("order_number", unique=True)
        mongo_db["orders"].create_index("user_id")
        mongo_db["orders"].create_index("status")
        mongo_db["orders"].create_index("id")
        mongo_db["order_events"].create_index("order_id")
        mongo_db["order_events"].create_index("id")

        for coll_name in ["users", "calls", "call_messages", "products", "faqs", "handoff_requests", "orders", "order_events"]:
            max_doc = mongo_db[coll_name].find_one(sort=[("id", -1)])
            max_id = int(max_doc["id"]) if max_doc and "id" in max_doc and max_doc["id"] is not None else 0
            mongo_db["counters"].update_one(
                {"_id": coll_name},
                {"$max": {"seq": max_id}},
                upsert=True,
            )

        seed_products()
        seed_faqs()
        seed_default_admin()
        seed_business_settings()
        seed_sample_orders()

        valid_statuses = ['pending_admin_confirmation', 'confirmed', 'completed', 'cancel_requested', 'cancelled', 'rejected']
        mongo_db["orders"].update_many(
            {"status": {"$nin": valid_statuses}},
            {"$set": {"status": "pending_admin_confirmation"}},
        )
    except Exception as exc:
        log.error("Notice during MongoDB initialization: %r", exc)


def get_or_create_call(session_id: str, user_id: int | None = None) -> int:
    """Get or create a call session document and return its integer primary key."""
    row = mongo_db["calls"].find_one({"session_id": session_id})
    if row:
        if user_id is not None and row.get("user_id") is None:
            mongo_db["calls"].update_one({"session_id": session_id}, {"$set": {"user_id": user_id}})
        return int(row["id"])
    new_id = get_next_sequence_value("calls")
    mongo_db["calls"].insert_one({
        "id": new_id,
        "session_id": session_id,
        "started_at": now(),
        "last_activity": now(),
        "status": "active",
        "outcome": "Resolved",
        "total_messages": 0,
        "user_id": user_id,
        "summary": None,
        "follow_up_status": "none",
        "detected_intent": None,
    })
    return new_id


def save_message(session_id: str, user_text: str, result: dict, user_id: int | None = None) -> None:
    """Persist conversation turn to MongoDB, update call summary and follow-up status."""
    try:
        call_id = get_or_create_call(session_id, user_id)
        intent = result.get("intent", "unknown")
        outcome = result.get("outcome", "Resolved")
        needs_handoff = result.get("needs_handoff", False) or outcome in ("Escalated", "Transferred")

        msg_id = get_next_sequence_value("call_messages")
        mongo_db["call_messages"].insert_one({
            "id": msg_id,
            "call_id": call_id,
            "timestamp": now(),
            "user_transcript": user_text,
            "ai_response": result.get("reply", ""),
            "detected_intent": intent,
            "outcome": outcome,
        })

        clean_user = user_text[:80].replace("'", "").replace('"', '').strip()
        summary = f"Caller inquiry ({intent}): {clean_user}" if clean_user else f"Inquiry about {intent}"

        call_doc = mongo_db["calls"].find_one({"id": call_id})
        current_follow_up = call_doc.get("follow_up_status") if call_doc else "none"
        if needs_handoff and current_follow_up not in ('needs_human', 'pending_staff'):
            new_follow_up = "needs_human"
        else:
            new_follow_up = current_follow_up or "none"

        update_fields = {
            "last_activity": now(),
            "outcome": outcome,
            "summary": summary,
            "follow_up_status": new_follow_up,
        }
        if not call_doc or not call_doc.get("detected_intent") or call_doc.get("detected_intent") == "general_inquiry":
            update_fields["detected_intent"] = intent

        mongo_db["calls"].update_one(
            {"id": call_id},
            {"$set": update_fields, "$inc": {"total_messages": 1}},
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
    """Load platform FAQs + admin-created FAQs + custom user FAQs from MongoDB."""
    admin_ids = [u["id"] for u in mongo_db["users"].find({"role": "admin"}, {"id": 1})]
    if user_id:
        filter_q = {"$or": [{"user_id": None}, {"user_id": {"$in": admin_ids}}, {"user_id": user_id}]}
    else:
        filter_q = {"$or": [{"user_id": None}, {"user_id": {"$in": admin_ids}}]}
    rows = list(mongo_db["faqs"].find(filter_q).sort("id", 1))
    if not rows:
        return "(No FAQs available)"
    return "\n\n".join(f"Q: {r.get('question', '')}\nA: {r.get('answer', '')}" for r in rows)


def load_products_context(user_id: int | None = None) -> str:
    """Load platform and user products from MongoDB formatted for Gemini system prompt."""
    admin_ids = [u["id"] for u in mongo_db["users"].find({"role": "admin"}, {"id": 1})]
    if user_id:
        filter_q = {"$or": [{"user_id": None}, {"user_id": {"$in": admin_ids}}, {"user_id": user_id}]}
    else:
        filter_q = {"$or": [{"user_id": None}, {"user_id": {"$in": admin_ids}}]}
    rows = list(mongo_db["products"].find(filter_q).sort("id", 1))
    if not rows:
        return "(No products currently listed in catalog)"
    return "\n".join(
        f"- {r.get('name', '')} ({r.get('category', '')}, {r.get('brand', '')}): PKR {r.get('price', 0):,} - {r.get('stock_status', '')} - {r.get('description', '')}"
        for r in rows
    )


# =====================================================================
# 5A. Business Settings, Orders Status Flow & Compliance Engine (Phase 4 & 7)
# =====================================================================
BUSINESS_SETTINGS_CACHE: dict[str, str] = {}


def refresh_business_settings_cache() -> dict[str, str]:
    """Load business settings from MongoDB into memory cache."""
    global BUSINESS_SETTINGS_CACHE
    try:
        rows = list(mongo_db["business_settings"].find({}, {"key": 1, "value": 1}))
        BUSINESS_SETTINGS_CACHE = {r["key"]: str(r.get("value", "")) for r in rows}
    except Exception as exc:
        log.warning("Failed to refresh business settings cache: %r", exc)
    return BUSINESS_SETTINGS_CACHE


def get_business_setting(key: str, default: str = "") -> str:
    """Get a business setting value, using in-memory cache with DB fallback."""
    if not BUSINESS_SETTINGS_CACHE:
        refresh_business_settings_cache()
    return BUSINESS_SETTINGS_CACHE.get(key, default)


def get_all_business_settings() -> dict[str, str]:
    """Return all business settings as a dictionary."""
    if not BUSINESS_SETTINGS_CACHE:
        refresh_business_settings_cache()
    return dict(BUSINESS_SETTINGS_CACHE)


LOCKED_ORDER_STATUSES = {"cancelled", "rejected", "completed"}

ORDER_TRANSITIONS = {
    "pending_admin_confirmation": {"confirmed", "rejected", "cancel_requested"},
    "confirmed": {"completed", "cancel_requested"},
    "cancel_requested": {"cancelled", "pending_admin_confirmation", "confirmed"},
    "cancelled": set(),
    "rejected": set(),
    "completed": set(),
}


def validate_and_apply_order_transition(
    conn=None,
    order_id: int | str = 0,
    target_status: str = "",
    actor_user_id: int | None = None,
    actor_role: str = "user",
    note: str | None = None,
    event_type: str = "status_change",
    **kwargs,
) -> dict:
    """
    Validate status transition rules for orders, enforce immutability on locked statuses,
    and append an immutable audit record in order_events.
    Raises HTTPException 404, 409 if invalid.
    """
    order = mongo_db["orders"].find_one(make_id_query(order_id))
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")

    current_status = order.get("status", "")
    is_locked = bool(order.get("is_locked")) or (current_status in LOCKED_ORDER_STATUSES)

    if is_locked:
        raise HTTPException(
            status_code=409,
            detail=f"Order #{order.get('order_number')} is in terminal locked status '{current_status}' and cannot be altered.",
        )

    allowed_targets = ORDER_TRANSITIONS.get(current_status, set())
    if target_status not in allowed_targets:
        raise HTTPException(
            status_code=409,
            detail=f"Invalid order status transition from '{current_status}' to '{target_status}'.",
        )

    if target_status == "rejected" and (not note or not note.strip()):
        raise HTTPException(
            status_code=400,
            detail="Rejection requires an explanatory admin note for the customer.",
        )

    new_is_locked = 1 if target_status in LOCKED_ORDER_STATUSES else 0
    now_str = now()

    update_fields = {
        "status": target_status,
        "is_locked": new_is_locked,
        "updated_at": now_str,
    }

    if target_status in ("confirmed", "rejected", "completed"):
        update_fields["decided_by"] = actor_user_id
        update_fields["decided_at"] = now_str
        update_fields["admin_note"] = note or order.get("admin_note")
    elif target_status == "cancel_requested":
        update_fields["cancel_requested_at"] = now_str
        update_fields["cancel_reason"] = note or order.get("cancel_reason")
    elif target_status == "cancelled":
        update_fields["decided_by"] = actor_user_id
        update_fields["decided_at"] = now_str
        update_fields["admin_note"] = note or order.get("admin_note")

    mongo_db["orders"].update_one(
        {"_id": order["_id"]},
        {"$set": update_fields},
    )

    eid = get_next_sequence_value("order_events")
    mongo_db["order_events"].insert_one({
        "id": eid,
        "order_id": order.get("id"),
        "actor_user_id": actor_user_id,
        "actor_role": actor_role,
        "event_type": event_type,
        "old_status": current_status,
        "new_status": target_status,
        "note": note,
        "created_at": now_str,
    })

    updated_order = mongo_db["orders"].find_one({"_id": order["_id"]})
    return serialize_doc(updated_order)


def check_service_conflict(
    conn=None,
    item_id: int | str = 0,
    preferred_datetime: str | None = None,
    exclude_order_id: int | None = None,
    **kwargs,
) -> dict:
    """Check if a service booking at preferred_datetime conflicts with an existing booking."""
    if not preferred_datetime or not str(preferred_datetime).strip():
        return {"has_conflict": False}

    prod = mongo_db["products"].find_one(make_id_query(item_id))
    if not prod or prod.get("item_type") != "service":
        return {"has_conflict": False}

    query_filter = {
        "item_id": prod.get("id"),
        "preferred_datetime": str(preferred_datetime).strip(),
        "status": {"$in": ["pending_admin_confirmation", "confirmed"]},
    }
    if exclude_order_id:
        query_filter["id"] = {"$ne": int(exclude_order_id)}

    conflicts = list(mongo_db["orders"].find(query_filter))
    if conflicts:
        conflict_list = [serialize_doc(c) for c in conflicts]
        return {
            "has_conflict": True,
            "warning": f"Double-booking detected: {len(conflicts)} other order(s) already scheduled for {prod.get('name')} at {preferred_datetime}.",
            "conflicts": conflict_list,
        }
    return {"has_conflict": False}


def create_or_update_handoff_request(
    conn=None,
    session_id: str = "",
    user_id: int | None = None,
    trigger_type: str = "user_requested",
    reason: str = "",
    priority: str = "normal",
    language: str = "en",
    contact_name: str | None = None,
    contact_phone: str | None = None,
    last_user_message: str | None = None,
    summary: str | None = None,
    **kwargs,
) -> int:
    """Create or update exactly one open handoff request per call session in MongoDB (Phase 7)."""
    now_str = now()
    call = mongo_db["calls"].find_one({"session_id": session_id})
    call_id = call.get("id") if call else None

    if not contact_name and user_id:
        user_row = mongo_db["users"].find_one(make_id_query(user_id))
        if user_row:
            contact_name = contact_name or user_row.get("full_name")
            contact_phone = contact_phone or user_row.get("phone")

    existing = mongo_db["handoff_requests"].find_one(
        {"session_id": session_id, "status": {"$in": ["open", "assigned"]}}
    )

    priority_ranks = {"normal": 1, "high": 2, "urgent": 3}

    if existing:
        curr_prio = existing.get("priority", "normal")
        new_prio = priority if priority_ranks.get(priority, 1) > priority_ranks.get(curr_prio, 1) else curr_prio
        update_fields = {
            "trigger_type": trigger_type,
            "reason": reason,
            "priority": new_prio,
            "language": language,
        }
        if contact_name:
            update_fields["contact_name"] = contact_name
        if contact_phone:
            update_fields["contact_phone"] = contact_phone
        if last_user_message:
            update_fields["last_user_message"] = last_user_message
        if summary:
            update_fields["summary"] = summary

        mongo_db["handoff_requests"].update_one({"_id": existing["_id"]}, {"$set": update_fields})
        handoff_id = existing.get("id")
    else:
        handoff_id = get_next_sequence_value("handoff_requests")
        mongo_db["handoff_requests"].insert_one({
            "id": handoff_id,
            "call_id": call_id,
            "session_id": session_id,
            "user_id": user_id,
            "contact_name": contact_name,
            "contact_phone": contact_phone,
            "trigger_type": trigger_type,
            "reason": reason,
            "priority": priority,
            "language": language,
            "last_user_message": last_user_message,
            "summary": summary,
            "status": "open",
            "created_at": now_str,
            "assigned_to": None,
            "staff_note": None,
            "assigned_at": None,
            "resolved_at": None,
            "resolved_by": None,
        })

    if call_id:
        call_update = {"follow_up_status": "needs_human", "detected_intent": trigger_type}
        if summary:
            call_update["summary"] = summary
        mongo_db["calls"].update_one({"id": call_id}, {"$set": call_update})

    return handoff_id


def build_system_instruction(user_id: int | None = None) -> str:
    """Build dynamic system instruction using business_settings and compliance rules."""
    b_name = get_business_setting("business_name", "our business")
    b_type = get_business_setting("business_type", "Products & Services")
    b_hours = get_business_setting("working_hours", "Monday to Saturday from 9:00 AM to 6:00 PM")
    faq_ctx = load_faqs_context(user_id)
    product_ctx = load_products_context(user_id)

    return f"""
You are the automated AI Calling Assistant for {b_name} ({b_type}).

KNOWLEDGE BASE:
1. Operational FAQs (policies, hours, payments):
{faq_ctx}

2. Product Catalog & Services (prices in PKR):
{product_ctx}

3. Working Hours: {b_hours}

COMPLIANCE & ETHICAL RULES:
1. AUTOMATED ASSISTANT IDENTITY: Always introduce yourself as an automated AI assistant. NEVER claim or imply that you are human.
2. NO UNAUTHORIZED COMMITMENTS: You have strictly zero authorization to offer refunds, custom price discounts, medical diagnosis, delivery guarantees, or instant order confirmations.
3. ORDER / APPOINTMENT REQUESTS: When a customer wants to place an order or book an appointment, inform them that an order REQUEST has been received and is waiting for admin confirmation. You must NEVER say "confirmed" or "booked" before an administrator confirms.
4. KNOWLEDGE ACCURACY: Answer using ONLY the knowledge base and catalog items above.
5. OFF-TOPIC PROTECTION: Politely decline any query outside {b_name} offerings: "I am sorry, I can only assist you with {b_name} products and services." Set intent to "unknown" and outcome to "Resolved".
6. HUMAN ESCALATION: If the caller explicitly requests a human representative, reports an urgent medical/legal/payment dispute, or has an unresolvable complaint, escalate to a Human Support Agent and set outcome to "Escalated".
7. SPOKEN VOICE STYLE: Speak in clear, professional English (or Urdu if the user speaks Urdu). Maximum 1 to 2 short conversational sentences suitable for a live telephone call.

Return ONLY a JSON object with these exact keys:
{{
  "response_text": "short reply displayed in the chat interface",
  "speech_text": "clean plain text reply used for text-to-speech audio",
  "intent": "order_status|business_hours|human_agent_request|faq|place_order|cancel_order|appointment_booking|unknown",
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
GUEST_TURNS: dict[str, int] = {}
GUEST_CALL_LIMIT = 5


def check_guest_limit(session_id: str, user_id: int | None, is_urdu: bool = False) -> dict | None:
    """Enforce guest turn limit for unauthenticated callers without account sign-up."""
    if user_id is not None:
        return None
    turns = GUEST_TURNS.get(session_id, 0) + 1
    GUEST_TURNS[session_id] = turns
    if turns > GUEST_CALL_LIMIT:
        b_name = get_business_setting("business_name", "our business")
        if is_urdu:
            msg = f"آپ نے مفت گیسٹ کال کی حد ({GUEST_CALL_LIMIT} سوالات) مکمل کر لی ہے۔ مزید گفتگو جاری رکھنے اور آرڈرز کے لیے برائے مہربانی اکاؤنٹ بنائیں یا لاگ ان کریں۔"
        else:
            msg = f"You have reached the free guest call limit of {GUEST_CALL_LIMIT} questions. Please sign up or log in to your account to continue placing orders and speaking with our AI assistant at {b_name}."
        return {
            "reply": msg,
            "speech": msg,
            "intent": "guest_limit_reached",
            "outcome": "Resolved",
            "guest_limit_reached": True,
            "turns": turns,
        }
    return None


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
    b_name = get_business_setting("business_name", "our business")
    return {
        "response_text": clean_text or f"Hello! How can I assist you with {b_name} today?",
        "speech_text": clean_text or f"Hello! How can I assist you with {b_name} today?",
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

        contents: list[Any] = []
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
        b_name = get_business_setting("business_name", "our business")
        reply = f"Hello! How can I assist you with {b_name} today?"

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
    item_type: str = Field(default="product", pattern=r"^(product|service)$")


class ProductUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=100)
    brand: str = Field(min_length=1, max_length=100)
    price: int = Field(ge=0)
    stock_status: str = Field(min_length=1, max_length=50)
    description: str = Field(min_length=0, max_length=1000, default="")
    item_type: str = Field(default="product", pattern=r"^(product|service)$")


class BusinessSettingsUpdate(BaseModel):
    business_name: str | None = None
    business_type: str | None = None
    greeting: str | None = None
    working_hours: str | None = None
    language_default: str | None = None
    max_order_quantity: int | None = None
    escalation_message: str | None = None
    business_hours_en: str | None = None
    business_hours_ur: str | None = None
    support_phone: str | None = None
    support_email: str | None = None
    office_address: str | None = None


class CreateOrderRequest(BaseModel):
    item_id: int
    quantity: int = Field(default=1, ge=1, le=100)
    preferred_datetime: str | None = None
    call_session_id: str | None = None
    customer_name: str | None = None
    customer_phone: str | None = None


class OrderCancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class OrderDecisionRequest(BaseModel):
    action: str = Field(pattern=r"^(confirm|reject|approve_cancel|reject_cancel|complete)$")
    admin_note: str | None = Field(default=None, max_length=1000)


class HandoffResolveRequest(BaseModel):
    staff_note: str | None = Field(default=None, max_length=1000)


class HandoffPriorityRequest(BaseModel):
    priority: str = Field(pattern=r"^(normal|high|urgent)$")


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

    row = mongo_db["users"].find_one(make_id_query(user_id))
    if not row or not row.get("is_active"):
        return None
    return serialize_doc(row)


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
class MongoJSONResponse(JSONResponse):
    """Custom JSONResponse ensuring any BSON ObjectId is serialized as a string."""
    def render(self, content) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
            default=lambda o: str(o) if isinstance(o, ObjectId) else (o.isoformat() if isinstance(o, (datetime, date)) else str(o)),
        ).encode("utf-8")


app = FastAPI(
    title="DigiBuzz AI Calling Agent",
    description="Multi-tenant Voice-based AI Customer Support Platform powered by Gemini and MongoDB Atlas.",
    version="3.0.0",
    default_response_class=MongoJSONResponse,
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

    # Check duplicate email
    if mongo_db["users"].find_one({"email": clean_email}):
        raise HTTPException(
            status_code=409,
            detail="An account with this email address already exists. Please sign in.",
        )
    # Check duplicate phone
    if mongo_db["users"].find_one({"phone": clean_phone}):
        raise HTTPException(
            status_code=409,
            detail="An account with this phone number already exists. Please sign in.",
        )

    password_hash = hash_password(req.password)
    new_id = get_next_sequence_value("users")
    user_doc = {
        "id": new_id,
        "email": clean_email,
        "phone": clean_phone,
        "full_name": req.full_name.strip(),
        "password_hash": password_hash,
        "role": "user",
        "is_active": 1,
        "created_at": now(),
        "last_login": None,
    }
    mongo_db["users"].insert_one(user_doc)

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

    row = mongo_db["users"].find_one({"$or": [{"email": ident_email}, {"phone": ident_phone}]})

    if not row or not verify_password(req.password, row.get("password_hash", "")):
        record_login_failure(rate_key)
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials. Please verify your email/phone and password.",
        )

    if not row.get("is_active"):
        raise HTTPException(
            status_code=403,
            detail="Your account has been suspended. Please contact platform support.",
        )

    clear_login_failures(rate_key)

    # Update last login
    mongo_db["users"].update_one({"_id": row["_id"]}, {"$set": {"last_login": now()}})

    user_id = row.get("id") or str(row["_id"])
    token = create_access_token(user_id, row.get("role", "user"), row.get("email", ""))
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
            "id": user_id,
            "email": row.get("email"),
            "phone": row.get("phone"),
            "full_name": row.get("full_name"),
            "role": row.get("role"),
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

    row = mongo_db["users"].find_one(make_id_query(current_user["id"]))
    if not row or not verify_password(req.current_password, row.get("password_hash", "")):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")

    new_hash = hash_password(req.new_password)
    mongo_db["users"].update_one({"_id": row["_id"]}, {"$set": {"password_hash": new_hash}})

    return {"status": "ok", "message": "Your password has been changed successfully."}


# =====================================================================
# 12. Admin Management Endpoints (OWASP Top 10 A01 RBAC)
# =====================================================================
@app.get("/api/admin/metrics")
async def get_admin_metrics(_admin: dict = Depends(require_admin)):
    """System-wide SaaS metrics for Admin dashboard."""
    def query():
        total_users = mongo_db["users"].count_documents({})
        active_users = mongo_db["users"].count_documents({"is_active": 1})
        suspended_users = total_users - active_users
        total_calls = mongo_db["calls"].count_documents({})
        total_messages = mongo_db["call_messages"].count_documents({})
        resolved = mongo_db["calls"].count_documents({"outcome": "Resolved"})
        escalated = mongo_db["calls"].count_documents({"outcome": "Escalated"})
        total_faqs = mongo_db["faqs"].count_documents({})
        total_products = mongo_db["products"].count_documents({})
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
        rows = list(mongo_db["users"].find({}, {"password_hash": 0}).sort("id", 1))
        user_list = []
        for r in rows:
            d = serialize_doc(r)
            uid = d.get("id")
            d["call_count"] = mongo_db["calls"].count_documents({"user_id": uid})
            d["faq_count"] = mongo_db["faqs"].count_documents({"user_id": uid})
            d["product_count"] = mongo_db["products"].count_documents({"user_id": uid})
            d["is_primary"] = is_primary_admin(user_id=uid, email=d.get("email"))
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
        if mongo_db["users"].find_one({"email": clean_email}):
            return 409, "An account with this email address already exists."
        if mongo_db["users"].find_one({"phone": clean_phone}):
            return 409, "An account with this phone number already exists."

        password_hash = hash_password(req.password)
        new_id = get_next_sequence_value("users")
        doc = {
            "id": new_id,
            "email": clean_email,
            "phone": clean_phone,
            "full_name": req.full_name.strip(),
            "password_hash": password_hash,
            "role": req.role,
            "is_active": 1 if req.is_active else 0,
            "created_at": now(),
            "last_login": None,
        }
        mongo_db["users"].insert_one(doc)
        res_doc = serialize_doc(doc)
        res_doc.pop("password_hash", None)
        res_doc["is_primary"] = is_primary_admin(user_id=new_id, email=clean_email)
        return 201, res_doc

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
        existing = mongo_db["users"].find_one(make_id_query(user_id))
        if not existing:
            return 404, "User not found."

        email_conflict = mongo_db["users"].find_one({"email": clean_email, "_id": {"$ne": existing["_id"]}})
        if email_conflict:
            return 409, "An account with this email address already exists."

        phone_conflict = mongo_db["users"].find_one({"phone": clean_phone, "_id": {"$ne": existing["_id"]}})
        if phone_conflict:
            return 409, "An account with this phone number already exists."

        update_fields = {
            "email": clean_email,
            "phone": clean_phone,
            "full_name": req.full_name.strip(),
            "role": req.role,
            "is_active": 1 if req.is_active else 0,
        }
        if req.password:
            update_fields["password_hash"] = hash_password(req.password)

        mongo_db["users"].update_one({"_id": existing["_id"]}, {"$set": update_fields})
        updated = mongo_db["users"].find_one({"_id": existing["_id"]})
        d = serialize_doc(updated)
        d.pop("password_hash", None)
        d["is_primary"] = is_primary_admin(user_id=d.get("id"), email=d.get("email"))
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
        res = mongo_db["users"].update_one(
            make_id_query(user_id),
            {"$set": {"is_active": 1 if req.is_active else 0}},
        )
        return res.matched_count > 0

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
        res = mongo_db["users"].update_one(make_id_query(user_id), {"$set": {"role": req.role}})
        return res.matched_count > 0

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
        res = mongo_db["users"].update_one(make_id_query(user_id), {"$set": {"password_hash": new_hash}})
        return res.matched_count > 0

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
        user = mongo_db["users"].find_one(make_id_query(user_id))
        if not user:
            return False
        uid = user.get("id")
        user_calls = list(mongo_db["calls"].find({"user_id": uid}))
        call_ids = [c["id"] for c in user_calls if "id" in c]
        if call_ids:
            mongo_db["call_messages"].delete_many({"call_id": {"$in": call_ids}})
        mongo_db["calls"].delete_many({"user_id": uid})
        mongo_db["faqs"].update_many({"user_id": uid}, {"$set": {"user_id": None}})
        mongo_db["products"].update_many({"user_id": uid}, {"$set": {"user_id": None}})
        mongo_db["users"].delete_one({"_id": user["_id"]})
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
    """Return system health, current engine, and tenant-scoped counts from MongoDB Atlas."""
    user = await get_optional_user(request)

    def query():
        if user and user.get("role") == "admin":
            faq_count = mongo_db["faqs"].count_documents({})
            product_count = mongo_db["products"].count_documents({})
        elif user:
            admin_ids = [u["id"] for u in mongo_db["users"].find({"role": "admin"}, {"id": 1})]
            faq_count = mongo_db["faqs"].count_documents({"$or": [{"user_id": None}, {"user_id": {"$in": admin_ids}}, {"user_id": user["id"]}]})
            product_count = mongo_db["products"].count_documents({"$or": [{"user_id": None}, {"user_id": {"$in": admin_ids}}, {"user_id": user["id"]}]})
        else:
            admin_ids = [u["id"] for u in mongo_db["users"].find({"role": "admin"}, {"id": 1})]
            faq_count = mongo_db["faqs"].count_documents({"$or": [{"user_id": None}, {"user_id": {"$in": admin_ids}}]})
            product_count = mongo_db["products"].count_documents({"$or": [{"user_id": None}, {"user_id": {"$in": admin_ids}}]})
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


def check_fast_knowledge_response(user_text: str, user_id: int | None = None) -> dict | None:
    """Fast-path lookup against MongoDB FAQs and Product Catalog for 0ms ultra-low latency response."""
    clean_query = user_text.lower().strip()
    clean_no_punc = re.sub(r"[^\w\s\u0600-\u06FF]", " ", clean_query).strip()
    words_all = set(clean_no_punc.split())
    if not words_all:
        return None

    # 1. First check FAQs (Direct exact & high-similarity normalized match)
    try:
        admin_ids = [u["id"] for u in mongo_db["users"].find({"role": "admin"}, {"id": 1})]
        faq_filter = {"$or": [{"user_id": None}, {"user_id": {"$in": admin_ids}}, {"user_id": user_id or 0}]}
        faq_rows = list(mongo_db["faqs"].find(faq_filter))

        norm_clean = re.sub(r"\s+", " ", clean_no_punc)

        # Direct exact question match (case/punctuation insensitive)
        for row in faq_rows:
            q_norm = re.sub(r"[^\w\s\u0600-\u06FF]", " ", row.get("question", "").lower()).strip()
            q_norm = re.sub(r"\s+", " ", q_norm)
            if norm_clean == q_norm:
                return {
                    "type": "faq",
                    "answer": row.get("answer", ""),
                    "intent": row.get("intent") or "faq_inquiry",
                }

        q_words = words_all - FAST_KNOWLEDGE_STOP_WORDS
        if q_words:
            expanded_q = _expand_knowledge_keywords(q_words)
            best_faq = None
            best_score = 0.0

            for row in faq_rows:
                f_words = set(re.findall(r"\b[a-z0-9]{3,}\b", row.get("question", "").lower())) - FAST_KNOWLEDGE_STOP_WORDS
                f_expanded = _expand_knowledge_keywords(f_words)
                common = expanded_q.intersection(f_expanded)
                if len(common) >= 1:
                    score = len(common) / max(len(q_words), 1)
                    if score > best_score:
                        best_score = score
                        best_faq = (row.get("question", ""), row.get("answer", ""), row.get("intent"))

            if best_faq and best_score >= 0.5:
                return {
                    "type": "faq",
                    "answer": best_faq[1],
                    "intent": best_faq[2] or "faq_inquiry",
                }

        # 2. Check Products
        if q_words:
            prod_filter = {"$or": [{"user_id": None}, {"user_id": {"$in": admin_ids}}, {"user_id": user_id or 0}]}
            prod_rows = list(mongo_db["products"].find(prod_filter))

            for row in prod_rows:
                name_clean = row.get("name", "").lower()
                name_words = set(re.findall(r"\b[a-z0-9]{3,}\b", name_clean)) - FAST_KNOWLEDGE_STOP_WORDS
                common_name = q_words.intersection(name_words)
                if len(common_name) >= 2 or (len(name_words) == 1 and len(common_name) == 1) or any(w in clean_query for w in name_words if len(w) >= 6):
                    status = "in stock" if "in" in str(row.get("stock_status", "")).lower() else "currently out of stock"
                    price_val = row.get("price", 0)
                    reply = f"The {row.get('name')} is {status} at wholesale rate PKR {price_val:,.0f}. {row.get('description', '')}"
                    return {
                        "type": "product",
                        "answer": reply,
                        "intent": "product_inquiry",
                    }
    except Exception as exc:
        log.warning("Fast knowledge lookup error: %r", exc)

    # 3. Check Conversational Intents (Strict matching only for pure conversational turns)
    b_name = get_business_setting("business_name", "our business")
    clean_phrase = " ".join(clean_no_punc.split())

    status_exact = {"how are you", "how are u", "how are you doing", "how do you do", "kaise ho", "kya haal hai", "kya haal", "theek ho"}
    if clean_phrase in status_exact:
        return {
            "type": "conversational",
            "answer": f"I am doing great, thank you! How may I assist you with {b_name} today?",
            "intent": "status",
        }

    identity_exact = {"who are you", "who is this", "what is your name", "tell me about yourself", "tell me about you", "ap kaun ho", "ap kaun hain", "tum kaun ho"}
    if clean_phrase in identity_exact:
        return {
            "type": "conversational",
            "answer": f"I am an automated AI Calling Assistant for {b_name}, here to assist you 24/7 with our catalog, services, and order inquiries.",
            "intent": "identity",
        }

    gratitude_exact = {"thanks", "thank you", "thank you so much", "thanks a lot", "shukriya", "jazakallah", "bohat shukriya"}
    if clean_phrase in gratitude_exact:
        return {
            "type": "conversational",
            "answer": "You are very welcome! Please let me know if there is anything else I can help you with today.",
            "intent": "gratitude",
        }

    catalog_exact = {"what do you sell", "what products do you have", "show catalog", "product catalog", "what products do you offer", "what are your products", "list of products", "services list"}
    if clean_phrase in catalog_exact:
        return {
            "type": "conversational",
            "answer": f"We offer a wide range of verified products and professional services at {b_name}. What item or service can I check for you?",
            "intent": "catalog_overview",
        }

    return None


WORD_TO_NUMBER = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "twenty": 20, "thirty": 30, "fifty": 50, "hundred": 100,
    "ek": 1, "aik": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5,
    "che": 6, "chay": 6, "saat": 7, "aath": 8, "ath": 8, "nau": 9, "das": 10,
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
    """Retrieve business hours from MongoDB business_settings collection."""
    key = "business_hours_ur" if is_urdu else "business_hours_en"
    try:
        row = mongo_db["business_settings"].find_one({"key": key})
        if row and row.get("value"):
            return str(row["value"])
    except Exception:
        pass
    if is_urdu:
        return "ہم پیر سے ہفتہ صبح 9 بجے سے رات 9 بجے تک کھلے رہتے ہیں۔ اتوار کو ہمارا دفتر بند ہوتا ہے۔"
    return "We are open Monday through Saturday from 9:00 AM to 9:00 PM. We are closed on Sundays."


def lookup_order_status(text: str, user_id: int | None = None) -> dict | None:
    """Direct database lookup for order status queries in MongoDB Atlas."""
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
        row = None
        if order_num:
            row = mongo_db["orders"].find_one({
                "$or": [
                    {"order_number": order_num},
                    {"order_number": {"$regex": re.escape(digits), "$options": "i"}}
                ]
            })

        if not row and user_id:
            row = mongo_db["orders"].find_one({"user_id": user_id}, sort=[("id", -1)])

        if not row and has_keyword and not order_num:
            row = mongo_db["orders"].find_one({}, sort=[("id", -1)])

        if row:
            status_raw = row.get("status", "")
            status_map_en = {
                "pending_admin_confirmation": "waiting for admin confirmation",
                "confirmed": "confirmed by administrator",
                "completed": "completed",
                "rejected": "rejected by administrator",
                "cancel_requested": "under review for cancellation",
                "cancelled": "cancelled",
            }
            status_map_ur = {
                "pending_admin_confirmation": "ایڈمن کی تصدیق کا منتظر",
                "confirmed": "ایڈمن سے تصدیق شدہ",
                "completed": "مکمل ہو چکا",
                "rejected": "ایڈمن سے مسترد شدہ",
                "cancel_requested": "منسوخی کی درخواست کے زیرِ جائزہ",
                "cancelled": "منسوخ ہو چکا",
            }
            status_en = status_map_en.get(status_raw, status_raw.replace("_", " "))
            status_ur = status_map_ur.get(status_raw, status_raw)
            total_val = row.get("total_pkr") or 0
            if is_urdu:
                reply = f"آپ کے آرڈر {row.get('order_number')} کا سٹیٹس اس وقت '{status_ur}' ہے۔ کل رقم {total_val:,.0f} روپے ہے۔"
            else:
                reply = f"The status of order {row.get('order_number')} is currently {status_en}. Total amount is PKR {total_val:,.0f}."
            return {
                "reply": reply,
                "language": "ur" if is_urdu else "en",
                "intent": "order_status",
                "outcome": "Resolved",
                "needs_handoff": False,
                "confidence": 1.0,
            }
    except Exception as exc:
        log.warning("Order lookup error: %r", exc)
    return None


def check_instant_fast_path(text: str, session_id: str, user_id: int | None = None) -> dict | None:
    """Instant 0ms deterministic handler for greetings, hours, bye, orders, handoff, confirmations, quantities, and cached FAQs."""
    clean = text.lower().strip()
    is_urdu = bool(ARABIC_SCRIPT.search(text)) or any(w in clean.split() for w in ["kya", "mera", "karo", "hai", "hain", "mujhe", "batao", "chahiye", "kahan", "kaise", "ap", "aap", "shukriya", "theek", "kardo"])
    words = clean.split()
    word_count = len(words)
    b_name = get_business_setting("business_name", "our support")

    # 1. Human agent escalation & sensitive emergency keywords (Phase 7 Compliance)
    sensitive_urgent_keywords = [
        "emergency", "fraud", "police", "lawyer", "legal", "scam", "chori", "court",
        "police bulao", "dhoka", "فراڈ", "ایمرجنسی", "پولیس", "وکیل", "قانونی", "دھوکہ"
    ]
    complaint_high_keywords = [
        "supervisor", "manager", "complaint", "shikayat", "dispute", "bad service",
        "ruined", "bakwas", "شکایت", "منیجر", "خراب سروس"
    ]
    human_keywords = [
        "talk to human", "speak with agent", "agent se baat", "human agent",
        "insan se baat", "operator", "customer service representative",
        "transfer call", "live agent", "kisi bande se", "representative",
        "human support", "real person", "support agent", "live support",
        "connect to live", "connect to",
        "نمائندہ", "نمائندے", "انسان سے بات", "ایجنٹ سے بات", "بات کرواؤ", "بات کراؤ",
        "کسٹمر سروس", "کسٹمر کیئر"
    ]

    is_urgent = any(k in clean for k in sensitive_urgent_keywords)
    is_complaint = any(k in clean for k in complaint_high_keywords)
    is_human_req = any(k in clean for k in human_keywords)

    if is_urgent or is_complaint or is_human_req:
        priority = "urgent" if is_urgent else ("high" if is_complaint else "high")
        trigger_type = "emergency_legal_dispute" if is_urgent else ("complaint_escalation" if is_complaint else "human_agent_request")
        reason = f"User triggered human escalation: {clean[:100]}"
        try:
            create_or_update_handoff_request(
                session_id=session_id,
                user_id=user_id,
                trigger_type=trigger_type,
                reason=reason,
                priority=priority,
                language="ur" if is_urdu else "en",
                last_user_message=text,
                summary=f"Caller requested human assistance ({priority} priority).",
            )
        except Exception as e:
            log.warning("Handoff record error: %r", e)

        if is_urdu:
            reply = "جی بالکل، میں آپ کی کال کسٹمر سپورٹ نمائندے کو ٹرانسفر کر رہی ہوں۔ براہ کرم ہولڈ کیجیے۔"
        else:
            default_esc = "I will connect you directly with a human staff member to assist you further."
            reply = get_business_setting("escalation_message", default_esc)
        return {
            "reply": reply,
            "language": "ur" if is_urdu else "en",
            "intent": "human_agent_request",
            "outcome": "Escalated",
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

    # 3. Order status lookup or cancellation request
    cancel_keywords = ["cancel my order", "order cancel", "cancel order", "آرڈر کینسل", "ارڈر کینسل", "منسوخ", "cancel kar do", "cancel kardo"]
    if any(k in clean for k in cancel_keywords):
        m = re.search(r"\b(?:ord(?:er)?[- ]?)?(\d{4,6})\b", clean, re.IGNORECASE)
        digits = m.group(1) if m else None
        order_num = f"ORD-{digits}" if digits else None
        try:
            order_row = None
            if order_num:
                order_row = mongo_db["orders"].find_one({"order_number": order_num})
            elif user_id:
                order_row = mongo_db["orders"].find_one({"user_id": user_id}, sort=[("id", -1)])
            else:
                order_row = mongo_db["orders"].find_one({}, sort=[("id", -1)])

            if order_row:
                o_num = order_row.get("order_number")
                st = order_row.get("status")
                if order_row.get("is_locked") or st in LOCKED_ORDER_STATUSES:
                    if is_urdu:
                        reply = f"آرڈر نمبر {o_num} پہلے ہی {st} ہے اور اسے منسوخ نہیں کیا جا سکتا۔"
                    else:
                        reply = f"Order #{o_num} is already {st.replace('_', ' ')} and cannot be cancelled."
                elif st == "cancel_requested":
                    if is_urdu:
                        reply = f"آرڈر نمبر {o_num} کی منسوخی کی درخواست پہلے ہی ایڈمن کے پاس زیرِ جائزہ ہے۔"
                    else:
                        reply = f"A cancellation request for order #{o_num} is already pending admin review."
                else:
                    validate_and_apply_order_transition(
                        order_id=order_row.get("id"),
                        target_status="cancel_requested",
                        actor_user_id=user_id,
                        actor_role="customer",
                        note="Customer requested cancellation via voice call",
                        event_type="cancel_requested",
                    )
                    if is_urdu:
                        reply = f"آپ کے آرڈر نمبر {o_num} کی منسوخی کی درخواست جمع کر دی گئی ہے اور ایڈمن کی منظوری کی منتظر ہے۔"
                    else:
                        reply = f"A cancellation request for order #{o_num} has been submitted and is waiting for admin approval."
                return {
                    "reply": reply,
                    "language": "ur" if is_urdu else "en",
                    "intent": "cancel_order",
                    "outcome": "Resolved",
                    "needs_handoff": False,
                    "confidence": 1.0,
                }
        except Exception as exc:
            log.warning("Fast path cancel order error: %r", exc)

    # 4. Check Order status direct match
    order_status_result = lookup_order_status(text, user_id)
    if order_status_result:
        return order_status_result

    # 5. Greetings
    greeting_keywords = ["hello", "hi", "hey", "good morning", "good afternoon", "good evening", "salam", "assalam", "aoa", "slam", "اسلام علیکم", "سلام"]
    if any(k in clean for k in greeting_keywords) and word_count <= 6:
        default_greeting_en = f"Hello! Welcome to {b_name} automated customer assistance. How may I assist you with our catalog and services today?"
        greeting_en = get_business_setting("greeting", default_greeting_en)
        greeting_ur = f"اسلام علیکم! {b_name} میں خوش آمدید۔ آج میں آپ کی کیا مدد کر سکتی ہوں؟"
        return {
            "reply": greeting_ur if is_urdu else greeting_en,
            "language": "ur" if is_urdu else "en",
            "intent": "greeting",
            "outcome": "Resolved",
            "needs_handoff": False,
            "confidence": 1.0,
        }

    # 6. Farewells
    farewell_keywords = ["bye", "goodbye", "good bye", "see you", "take care", "allah hafiz", "khuda hafiz", "shukriya", "thanks", "thank you", "الله حافظ", "خدا حافظ", "شکریہ"]
    if any(k in clean for k in farewell_keywords) and word_count <= 6:
        farewell_en = f"Thank you for contacting {b_name}. Have a wonderful day ahead! Goodbye."
        farewell_ur = f"{b_name} سے رابطہ کرنے کا بہت شکریہ۔ اپنا خیال رکھیے گا، اللہ حافظ!"
        return {
            "reply": farewell_ur if is_urdu else farewell_en,
            "language": "ur" if is_urdu else "en",
            "intent": "farewell",
            "outcome": "Resolved",
            "needs_handoff": False,
            "confidence": 1.0,
        }

    # 7. Affirmations / Confirmations
    confirm_words = ["yes", "yeah", "yep", "sure", "ok", "okay", "theek", "theek hai", "haan", "jee", "ji", "confirm", "confirm karo", "جی", "ہاں", "ٹھیک ہے", "بالکل"]
    negate_words = ["no", "nah", "cancel", "nahi", "na", "mat karo", "نہ", "نہیں", "کینسل"]
    if clean in confirm_words:
        reply_en = "Confirmed. I have noted that. What item or service would you like to proceed with?"
        reply_ur = "جی بالکل، میں نے نوٹ کر لیا ہے۔ بتائیے کہ آپ کس چیز کا آرڈر دینا چاہتے ہیں؟"
        return {
            "reply": reply_ur if is_urdu else reply_en,
            "language": "ur" if is_urdu else "en",
            "intent": "confirmation",
            "confirmed": True,
            "outcome": "Resolved",
            "needs_handoff": False,
            "confidence": 1.0,
        }
    if clean in negate_words:
        reply_en = "Understood. The action has been cancelled. How else can I assist you today?"
        reply_ur = "سمجھ گئی، کارروائی منسوخ کر دی گئی ہے۔ بتائیے میں مزید کیا مدد کر سکتی ہوں؟"
        return {
            "reply": reply_ur if is_urdu else reply_en,
            "language": "ur" if is_urdu else "en",
            "intent": "confirmation",
            "confirmed": False,
            "outcome": "Resolved",
            "needs_handoff": False,
            "confidence": 1.0,
        }

    # 8. Numeric Quantity Verification
    qty = parse_quantity(text)
    if qty is not None and word_count <= 4:
        reply_en = f"I have noted {qty} units. Would you like me to submit this order request for administrator confirmation?"
        reply_ur = f"میں نے {qty} عدد نوٹ کر لیے ہیں۔ کیا میں یہ آرڈر ایڈمن کی تصدیق کے لیے جمع کر دوں؟"
        return {
            "reply": reply_ur if is_urdu else reply_en,
            "language": "ur" if is_urdu else "en",
            "intent": "quantity_verification",
            "quantity": qty,
            "outcome": "Resolved",
            "needs_handoff": False,
            "confidence": 1.0,
        }

    # 9. Fast Knowledge Base & FAQ Check
    fast_kb = check_fast_knowledge_response(text, user_id)
    if fast_kb:
        return {
            "reply": fast_kb["answer"],
            "language": "ur" if is_urdu else "en",
            "intent": fast_kb.get("intent", "faq_inquiry"),
            "outcome": "Resolved",
            "needs_handoff": False,
            "confidence": 0.95,
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

    b_name = get_business_setting("business_name", "our business")
    b_type = get_business_setting("business_type", "Products & Services")
    instruction = f"""You are the automated AI Calling Assistant for {b_name} ({b_type}) speaking live on a phone call.
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


def build_voice_system_instruction(user_id: int | None = None) -> str:
    """Build concise, conversational instructions tailored for low-latency live voice calls."""
    b_name = get_business_setting("business_name", "our business")
    b_type = get_business_setting("business_type", "Products & Services")
    b_hours = get_business_setting("working_hours", "Monday to Saturday from 9:00 AM to 6:00 PM")
    faq_ctx = load_faqs_context(user_id)
    product_ctx = load_products_context(user_id)
    return f"""
You are the automated AI Calling Assistant speaking live with a customer for {b_name} ({b_type}).

KNOWLEDGE BASE:
1. Operational FAQs (policies, delivery, payments):
{faq_ctx}

2. Product Catalog & Services (prices in PKR):
{product_ctx}

3. Working Hours: {b_hours}

CRITICAL RULES FOR LIVE VOICE CALL:
1. AUTOMATED IDENTITY: Introduce yourself as an automated assistant. Never claim to be human.
2. NO UNAUTHORIZED COMMITMENTS: Never promise refunds, unauthorized discounts, medical advice, delivery guarantees, or instant confirmations.
3. ORDERS & APPOINTMENTS: Always inform callers that an order or appointment is a REQUEST waiting for admin confirmation. Never say "confirmed" or "booked" before admin confirms.
4. ACCURACY: Answer using ONLY the FAQ knowledge base and Catalog above.
5. SPOKEN VOICE FORMAT:
   - Speak in clear, natural, conversational sentences suitable for live Text-to-Speech audio.
   - Do NOT use markdown asterisks (*), hashtags (#), bullet points (-), emojis, or JSON formatting.
   - Keep answers concise and direct: maximum 1 to 2 short sentences.
6. LANGUAGE: Speak in English or conversational Urdu depending on caller's language.
7. ESCALATION: If the caller asks for a human agent or has an emergency/dispute, state: "I will connect you directly with a human staff member to assist you further."
8. OFF-TOPIC: Politely decline any queries outside {b_name} offerings: "I am sorry, I can only assist you with {b_name} products and services."
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

            contents: list[Any] = []
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
            row = mongo_db["users"].find_one(make_id_query(payload["sub"]))
            if row and row.get("is_active"):
                opt_user = serialize_doc(row)
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

            # 1b. Check Guest Limits for unauthenticated callers without sign-up
            is_urdu = bool(ARABIC_SCRIPT.search(user_text))
            guest_check = check_guest_limit(session_id, user_id, is_urdu)
            if guest_check:
                limit_text = guest_check["reply"]
                words = limit_text.split(" ")
                for i, w in enumerate(words):
                    token = w + (" " if i < len(words) - 1 else "")
                    await websocket.send_text(json.dumps({
                        "type": "text_delta",
                        "delta": token,
                    }))
                    await asyncio.sleep(0.02)
                audio_data = await synthesize_edge_tts(guest_check["speech"], DEFAULT_URDU_VOICE if is_urdu else DEFAULT_ENGLISH_VOICE)
                if audio_data:
                    await websocket.send_text(json.dumps({"type": "audio_start"}))
                    await websocket.send_bytes(audio_data)
                await websocket.send_text(json.dumps({
                    "type": "guest_limit",
                    "message": limit_text,
                    "guest_limit_reached": True,
                }))
                await websocket.send_text(json.dumps({
                    "type": "meta",
                    "intent": "guest_limit_reached",
                    "outcome": "Resolved",
                }))
                return

            # 2. IMMEDIATE 0ms FILLER AUDIO:
            # Transmit pre-cached natural filler phrase audio immediately so the caller hears voice within milliseconds!
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
            b_name = get_business_setting("business_name", "our business")
            clean_reply = full_text.strip() or f"Thank you for contacting {b_name}. How may I assist you?"
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

    # Check guest turn limit for unauthenticated callers
    is_urdu = bool(ARABIC_SCRIPT.search(text))
    guest_check = check_guest_limit(req.session_id, user_id, is_urdu)
    if guest_check:
        return guest_check

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

    # Check guest turn limit for unauthenticated callers
    is_urdu = bool(ARABIC_SCRIPT.search(text))
    guest_check = check_guest_limit(req.session_id, user_id, is_urdu)
    if guest_check:
        async def guest_stream_generator():
            yield f"data: {json.dumps({'type': 'sentence', 'text': guest_check['reply'], 'idx': 0}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'guest_limit', 'message': guest_check['reply'], 'guest_limit_reached': True}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'meta', 'intent': 'guest_limit_reached', 'outcome': 'Resolved', 'confidence': 1.0, 'needs_handoff': False}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return StreamingResponse(guest_stream_generator(), media_type="text/event-stream")

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
    """Register a human-agent handoff request in MongoDB Atlas."""
    user = await get_optional_user(request)
    user_id = user["id"] if user else None
    call = mongo_db["calls"].find_one({"session_id": req.session_id})
    call_id = call.get("id") if call else None
    handoff_id = get_next_sequence_value("handoff_requests")
    mongo_db["handoff_requests"].insert_one({
        "id": handoff_id,
        "call_id": call_id,
        "session_id": req.session_id,
        "user_id": user_id,
        "trigger_type": req.trigger_type,
        "reason": req.reason or "Live agent escalation",
        "priority": "normal",
        "created_at": now(),
        "status": "pending",
    })
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
    """Return top suggested questions as quick-click slider chips from MongoDB."""
    user = await get_optional_user(request)

    def query():
        if user and user.get("role") == "admin":
            rows = list(mongo_db["faqs"].find({}, {"question": 1, "intent": 1}).sort("id", 1).limit(limit))
        elif user:
            admin_ids = [u["id"] for u in mongo_db["users"].find({"role": "admin"}, {"id": 1})]
            rows = list(mongo_db["faqs"].find(
                {"$or": [{"user_id": None}, {"user_id": {"$in": admin_ids}}, {"user_id": user["id"]}]},
                {"question": 1, "intent": 1}
            ).sort("id", 1).limit(limit))
        else:
            admin_ids = [u["id"] for u in mongo_db["users"].find({"role": "admin"}, {"id": 1})]
            rows = list(mongo_db["faqs"].find(
                {"$or": [{"user_id": None}, {"user_id": {"$in": admin_ids}}]},
                {"question": 1, "intent": 1}
            ).sort("id", 1).limit(limit))
        return [{"question": r.get("question"), "intent": r.get("intent")} for r in rows]

    return {"questions": await asyncio.to_thread(query)}


@app.get("/api/faqs/all")
async def get_all_faqs(admin: dict = Depends(require_admin)):
    """Return FAQs: accessible only to Administrators."""
    def query():
        rows = list(mongo_db["faqs"].find().sort("id", 1))
        out = []
        for r in rows:
            d = serialize_doc(r)
            uid = d.get("user_id")
            if uid:
                u = mongo_db["users"].find_one(make_id_query(uid))
                d["owner_name"] = u.get("full_name", "Platform") if u else "Platform"
                d["owner_email"] = u.get("email") if u else None
            else:
                d["owner_name"] = "Platform"
                d["owner_email"] = None
            out.append(d)
        return out

    return await asyncio.to_thread(query)


@app.post("/api/faqs", status_code=201)
async def create_faq(faq: FAQCreate, admin: dict = Depends(require_admin)):
    """Add a new FAQ entry to platform knowledge base (Admin only)."""
    user_id = admin["id"]

    def query():
        new_id = get_next_sequence_value("faqs")
        doc = {
            "id": new_id,
            "intent": faq.intent.strip(),
            "question": faq.question.strip(),
            "answer": faq.answer.strip(),
            "user_id": user_id,
        }
        mongo_db["faqs"].insert_one(doc)
        return serialize_doc(doc)

    res = await asyncio.to_thread(query)
    try:
        refresh_knowledge_base()
    except Exception as exc:
        log.warning("Notice refreshing knowledge base after FAQ create: %r", exc)
    return res


@app.put("/api/faqs/{faq_id}")
async def update_faq(faq_id: int, faq: FAQUpdate, admin: dict = Depends(require_admin)):
    """Update FAQ entry in platform knowledge base (Admin only)."""
    def query():
        existing = mongo_db["faqs"].find_one(make_id_query(faq_id))
        if not existing:
            return 404

        mongo_db["faqs"].update_one(
            {"_id": existing["_id"]},
            {"$set": {
                "intent": faq.intent.strip(),
                "question": faq.question.strip(),
                "answer": faq.answer.strip(),
            }}
        )
        updated = mongo_db["faqs"].find_one({"_id": existing["_id"]})
        return serialize_doc(updated)

    result = await asyncio.to_thread(query)
    if result == 404:
        raise HTTPException(status_code=404, detail="FAQ not found.")

    try:
        refresh_knowledge_base()
    except Exception as exc:
        log.warning("Notice refreshing knowledge base after FAQ update: %r", exc)
    return result


@app.delete("/api/faqs/{faq_id}", status_code=204)
async def delete_faq(faq_id: int, admin: dict = Depends(require_admin)):
    """Delete FAQ entry from MongoDB (Admin only)."""
    def query():
        existing = mongo_db["faqs"].find_one(make_id_query(faq_id))
        if not existing:
            return 404
        mongo_db["faqs"].delete_one({"_id": existing["_id"]})
        return 204

    code = await asyncio.to_thread(query)
    if code == 404:
        raise HTTPException(status_code=404, detail="FAQ not found.")

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
        query_filter = {}
        if not user:
            admin_ids = [u["id"] for u in mongo_db["users"].find({"role": "admin"}, {"id": 1})]
            query_filter["$or"] = [{"user_id": None}, {"user_id": {"$in": admin_ids}}]
        elif user.get("role") != "admin":
            admin_ids = [u["id"] for u in mongo_db["users"].find({"role": "admin"}, {"id": 1})]
            query_filter["$or"] = [{"user_id": None}, {"user_id": {"$in": admin_ids}}, {"user_id": user["id"]}]

        if category and category.strip():
            query_filter["category"] = {"$regex": f"^{re.escape(category.strip())}$", "$options": "i"}

        rows = list(mongo_db["products"].find(query_filter).sort("id", 1))
        out = []
        for r in rows:
            d = serialize_doc(r)
            uid = d.get("user_id")
            if uid:
                u = mongo_db["users"].find_one(make_id_query(uid))
                d["owner_name"] = u.get("full_name", "Platform") if u else "Platform"
            else:
                d["owner_name"] = "Platform"
            d["item_type"] = d.get("item_type") or "product"
            out.append(d)
        return out

    return await asyncio.to_thread(query)


@app.post("/api/products", status_code=201)
async def create_product(product: ProductCreate, current_user: dict = Depends(get_current_user)):
    """Add a new product entry owned by current user (or platform if admin)."""
    user_id = current_user["id"]

    def query():
        new_id = get_next_sequence_value("products")
        item_type = getattr(product, "item_type", "product") or "product"
        doc = {
            "id": new_id,
            "name": product.name.strip(),
            "category": product.category.strip(),
            "brand": product.brand.strip(),
            "price": product.price,
            "stock_status": product.stock_status.strip(),
            "description": product.description.strip(),
            "item_type": item_type,
            "user_id": user_id,
        }
        mongo_db["products"].insert_one(doc)
        return serialize_doc(doc)

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
        existing = mongo_db["products"].find_one(make_id_query(product_id))
        if not existing:
            return 404
        if current_user["role"] != "admin" and existing.get("user_id") != current_user["id"]:
            return 403

        item_type = getattr(product, "item_type", "product") or "product"
        update_fields = {
            "name": product.name.strip(),
            "category": product.category.strip(),
            "brand": product.brand.strip(),
            "price": product.price,
            "stock_status": product.stock_status.strip(),
            "description": product.description.strip(),
            "item_type": item_type,
        }
        mongo_db["products"].update_one({"_id": existing["_id"]}, {"$set": update_fields})
        updated = mongo_db["products"].find_one({"_id": existing["_id"]})
        return serialize_doc(updated)

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
    """Delete product from MongoDB Atlas."""
    def query():
        existing = mongo_db["products"].find_one(make_id_query(product_id))
        if not existing:
            return 404
        if current_user["role"] != "admin" and existing.get("user_id") != current_user["id"]:
            return 403
        mongo_db["products"].delete_one({"_id": existing["_id"]})
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
    """Return recent calls from MongoDB: Regular users strictly see their own; Admins can see all."""
    user = await get_optional_user(request)

    def query():
        query_filter = {}
        if user and user.get("role") == "admin":
            if target_user_id:
                query_filter["user_id"] = target_user_id
        elif user:
            query_filter["user_id"] = user["id"]
        else:
            query_filter["user_id"] = None

        rows = list(mongo_db["calls"].find(query_filter).sort("id", -1).limit(limit))
        out = []
        for r in rows:
            d = serialize_doc(r)
            uid = d.get("user_id")
            if uid:
                u = mongo_db["users"].find_one(make_id_query(uid))
                d["caller_name"] = u.get("full_name") if u else None
                d["caller_email"] = u.get("email") if u else None
            else:
                d["caller_name"] = None
                d["caller_email"] = None
            out.append(d)
        return out

    return await asyncio.to_thread(query)


@app.get("/api/history")
async def get_conversation_history(
    request: Request,
    session_id: str = Query(..., pattern=SESSION_ID_PATTERN),
):
    """Return full conversation transcript for a session, checking tenant ownership."""
    user = await get_optional_user(request)

    def query():
        call = mongo_db["calls"].find_one({"session_id": session_id})
        if not call:
            return 404, []

        if call.get("user_id") is not None:
            if not user or (user.get("role") != "admin" and user.get("id") != call.get("user_id")):
                return 403, []

        rows = list(mongo_db["call_messages"].find({"call_id": call.get("id")}).sort("id", 1))
        return 200, serialize_docs(rows)

    status_code, data = await asyncio.to_thread(query)
    if status_code == 404:
        raise HTTPException(status_code=404, detail="Call session not found.")
    if status_code == 403:
        raise HTTPException(status_code=403, detail="Access denied. You do not own this call transcript.")

    return data


@app.post("/api/calls/sync")
async def sync_client_calls(req: SyncCallRequest, request: Request):
    """Sync client-side recorded session logs into MongoDB database with user binding."""
    user = await get_optional_user(request)
    user_id = user["id"] if user else None

    def _sync():
        existing = mongo_db["calls"].find_one({"session_id": req.session_id})
        if existing and existing.get("user_id") is not None:
            if user_id != existing.get("user_id") and (not user or user.get("role") != "admin"):
                return {"status": "error", "detail": "Access denied. Cannot sync to another user's session."}

        call_id = get_or_create_call(req.session_id, user_id)
        for m in req.messages:
            exists = mongo_db["call_messages"].find_one({
                "call_id": call_id,
                "timestamp": m.timestamp,
                "user_transcript": m.user_transcript,
            })
            if not exists:
                msg_id = get_next_sequence_value("call_messages")
                mongo_db["call_messages"].insert_one({
                    "id": msg_id,
                    "call_id": call_id,
                    "timestamp": m.timestamp,
                    "user_transcript": m.user_transcript,
                    "ai_response": m.ai_response,
                    "detected_intent": m.detected_intent,
                    "outcome": m.outcome,
                })
        msg_count = mongo_db["call_messages"].count_documents({"call_id": call_id})
        mongo_db["calls"].update_one(
            {"id": call_id},
            {"$set": {
                "last_activity": req.last_activity or now(),
                "total_messages": msg_count,
                "outcome": req.outcome,
            }},
        )
        return {"status": "synced", "session_id": req.session_id}

    try:
        return await asyncio.to_thread(_sync)
    except Exception as exc:
        log.error("Call sync failed: %r", exc)
        return {"status": "error", "detail": str(exc)}


# =====================================================================
# 17B. Business Settings Endpoints (Phase 4 Generic Store & Services)
# =====================================================================
@app.get("/api/business-settings")
async def get_business_settings_endpoint():
    """Return all platform business settings."""
    return await asyncio.to_thread(get_all_business_settings)


@app.put("/api/business-settings")
async def update_business_settings_endpoint(settings: BusinessSettingsUpdate, admin: dict = Depends(require_admin)):
    """Update platform business settings (Admin only)."""
    def _update():
        updates = {k: v for k, v in settings.model_dump().items() if v is not None}
        if not updates:
            return get_all_business_settings()
        for k, v in updates.items():
            mongo_db["business_settings"].update_one(
                {"key": k},
                {"$set": {"key": k, "value": str(v)}},
                upsert=True,
            )
        refresh_business_settings_cache()
        CACHED_STREAM_SYSTEM_INSTRUCTION.clear()
        return get_all_business_settings()

    return await asyncio.to_thread(_update)


# =====================================================================
# 18. Customer & Admin Orders Endpoints (Phase 4 Orders & Audit Flow)
# =====================================================================
@app.post("/api/orders")
async def create_order_endpoint(req: CreateOrderRequest, request: Request):
    """
    Create a new order / appointment booking in MongoDB Atlas.
    Order is ALWAYS created with status='pending_admin_confirmation'.
    """
    user = await get_optional_user(request)
    user_id = user["id"] if user else None

    def _create():
        prod = mongo_db["products"].find_one(make_id_query(req.item_id))
        if not prod:
            raise HTTPException(status_code=404, detail="Item not found.")

        c_name = req.customer_name or (user["full_name"] if user else "Customer")
        c_phone = req.customer_phone or (user["phone"] if user else "")

        order_num = f"ORD-{random.randint(1000, 9999)}"
        while mongo_db["orders"].find_one({"order_number": order_num}):
            order_num = f"ORD-{random.randint(1000, 9999)}"

        unit_price = prod.get("price", 0)
        total_pkr = unit_price * req.quantity
        now_str = now()

        conflict_info = check_service_conflict(None, req.item_id, req.preferred_datetime)

        order_id = get_next_sequence_value("orders")
        order_doc = {
            "id": order_id,
            "order_number": order_num,
            "user_id": user_id,
            "call_session_id": req.call_session_id,
            "customer_name": c_name,
            "customer_phone": c_phone,
            "item_id": prod.get("id"),
            "item_name_snapshot": prod.get("name", "Item"),
            "unit_price_snapshot": unit_price,
            "quantity": req.quantity,
            "preferred_datetime": req.preferred_datetime,
            "total_pkr": total_pkr,
            "status": "pending_admin_confirmation",
            "is_locked": 0,
            "created_at": now_str,
            "updated_at": now_str,
            "cancel_requested_at": None,
            "cancel_reason": None,
            "decided_by": None,
            "decided_at": None,
            "admin_note": None,
        }
        mongo_db["orders"].insert_one(order_doc)

        event_id = get_next_sequence_value("order_events")
        mongo_db["order_events"].insert_one({
            "id": event_id,
            "order_id": order_id,
            "actor_user_id": user_id,
            "actor_role": "customer",
            "event_type": "order_created",
            "old_status": None,
            "new_status": "pending_admin_confirmation",
            "note": "Order request submitted waiting for admin confirmation",
            "created_at": now_str,
        })

        created_order = serialize_doc(order_doc)
        created_order["service_conflict"] = conflict_info
        return created_order

    return await asyncio.to_thread(_create)


@app.get("/api/orders/mine")
async def get_my_orders(current_user: dict = Depends(get_current_user)):
    """Return all orders belonging to authenticated customer with BOLA protection."""
    def _query():
        rows = list(mongo_db["orders"].find({"user_id": current_user["id"]}).sort("id", -1))
        out = []
        for r in rows:
            d = serialize_doc(r)
            item_id = d.get("item_id")
            if item_id:
                p = mongo_db["products"].find_one(make_id_query(item_id))
                d["category"] = p.get("category") if p else None
                d["item_type"] = p.get("item_type", "product") if p else "product"
            else:
                d["category"] = None
                d["item_type"] = "product"
            out.append(d)
        return out

    return await asyncio.to_thread(_query)


@app.post("/api/orders/{order_id}/cancel-request")
async def request_order_cancellation(order_id: int, req: OrderCancelRequest, current_user: dict = Depends(get_current_user)):
    """Request order cancellation for a customer's order."""
    def _cancel():
        order = mongo_db["orders"].find_one(make_id_query(order_id))
        if not order:
            raise HTTPException(status_code=404, detail="Order not found.")

        if current_user.get("role") != "admin" and order.get("user_id") != current_user["id"]:
            raise HTTPException(status_code=403, detail="Access denied. You do not own this order.")

        if order.get("is_locked") or order.get("status") in LOCKED_ORDER_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=f"Order #{order.get('order_number')} is in locked status '{order.get('status')}' and cannot be cancelled.",
            )

        if order.get("status") == "cancel_requested":
            raise HTTPException(
                status_code=409,
                detail=f"A cancellation request is already pending for order #{order.get('order_number')}.",
            )

        reason_str = req.reason.strip() if req.reason else "Customer requested cancellation via portal"
        return validate_and_apply_order_transition(
            order_id=order_id,
            target_status="cancel_requested",
            actor_user_id=current_user["id"],
            actor_role=current_user.get("role", "customer"),
            note=reason_str,
            event_type="cancel_requested",
        )

    return await asyncio.to_thread(_cancel)


@app.delete("/api/orders/{order_id}")
async def delete_order_prohibited(order_id: int):
    """Orders cannot be deleted; financial and audit trail is strictly immutable."""
    raise HTTPException(
        status_code=405,
        detail="Order deletion is prohibited. Orders are immutable audit records; use status transitions instead.",
    )


@app.get("/api/admin/orders")
async def list_admin_orders(
    status_filter: str | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None),
    customer_id: int | None = Query(default=None),
    admin: dict = Depends(require_admin),
):
    """Admin view: List all orders with filters, search, and double-booking warning."""
    def _query():
        query_filter = {}
        if status_filter and status_filter.strip():
            query_filter["status"] = status_filter.strip()
        if customer_id:
            query_filter["user_id"] = customer_id
        if search and search.strip():
            s = re.escape(search.strip())
            query_filter["$or"] = [
                {"order_number": {"$regex": s, "$options": "i"}},
                {"customer_name": {"$regex": s, "$options": "i"}},
                {"customer_phone": {"$regex": s, "$options": "i"}},
                {"item_name_snapshot": {"$regex": s, "$options": "i"}},
            ]

        rows = list(mongo_db["orders"].find(query_filter))
        # Custom status sort: pending_admin_confirmation first, then cancel_requested, then others, by id desc
        def sort_key(doc):
            st = doc.get("status")
            priority = 1 if st == "pending_admin_confirmation" else (2 if st == "cancel_requested" else 3)
            return (priority, -int(doc.get("id") or 0))

        rows.sort(key=sort_key)
        result = []
        for r in rows:
            d = serialize_doc(r)
            uid = d.get("user_id")
            if uid:
                u = mongo_db["users"].find_one(make_id_query(uid))
                d["user_email"] = u.get("email") if u else None
            else:
                d["user_email"] = None

            item_id = d.get("item_id")
            prod = mongo_db["products"].find_one(make_id_query(item_id)) if item_id else None
            d["item_type"] = prod.get("item_type", "product") if prod else "product"

            if d.get("item_type") == "service" and d.get("preferred_datetime"):
                d["double_booking_warning"] = check_service_conflict(
                    None, d["item_id"], d["preferred_datetime"], exclude_order_id=d.get("id")
                )
            else:
                d["double_booking_warning"] = {"has_conflict": False}
            result.append(d)
        return result

    return await asyncio.to_thread(_query)


@app.get("/api/admin/orders/summary")
async def get_admin_orders_summary(admin: dict = Depends(require_admin)):
    """Return summary counts of pending confirmation, cancel requests, and totals from MongoDB."""
    def _summary():
        pending = mongo_db["orders"].count_documents({"status": "pending_admin_confirmation"})
        cancel_req = mongo_db["orders"].count_documents({"status": "cancel_requested"})
        confirmed = mongo_db["orders"].count_documents({"status": "confirmed"})
        total = mongo_db["orders"].count_documents({})
        return {
            "pending_confirmation_count": pending,
            "cancel_requested_count": cancel_req,
            "confirmed_count": confirmed,
            "total_count": total,
        }

    return await asyncio.to_thread(_summary)


@app.post("/api/admin/orders/{order_id}/decision")
async def decide_order(order_id: int, req: OrderDecisionRequest, admin: dict = Depends(require_admin)):
    """Admin confirms, rejects, approves cancel, rejects cancel, or completes an order."""
    def _decide():
        order = mongo_db["orders"].find_one(make_id_query(order_id))
        if not order:
            raise HTTPException(status_code=404, detail="Order not found.")

        curr = order.get("status")
        action = req.action

        if action == "confirm":
            if curr != "pending_admin_confirmation":
                raise HTTPException(status_code=409, detail=f"Cannot confirm order in status '{curr}'.")
            return validate_and_apply_order_transition(
                order_id=order_id,
                target_status="confirmed",
                actor_user_id=admin["id"],
                actor_role="admin",
                note=req.admin_note,
                event_type="admin_confirmed",
            )

        elif action == "reject":
            if curr != "pending_admin_confirmation":
                raise HTTPException(status_code=409, detail=f"Cannot reject order in status '{curr}'.")
            if not req.admin_note or not req.admin_note.strip():
                raise HTTPException(status_code=400, detail="Rejection requires an explanatory admin note.")
            return validate_and_apply_order_transition(
                order_id=order_id,
                target_status="rejected",
                actor_user_id=admin["id"],
                actor_role="admin",
                note=req.admin_note.strip(),
                event_type="admin_rejected",
            )

        elif action == "approve_cancel":
            if curr != "cancel_requested":
                raise HTTPException(status_code=409, detail=f"Cannot approve cancellation for status '{curr}'.")
            return validate_and_apply_order_transition(
                order_id=order_id,
                target_status="cancelled",
                actor_user_id=admin["id"],
                actor_role="admin",
                note=req.admin_note,
                event_type="cancel_approved",
            )

        elif action == "reject_cancel":
            if curr != "cancel_requested":
                raise HTTPException(status_code=409, detail=f"Cannot reject cancellation for status '{curr}'.")
            last_event = mongo_db["order_events"].find_one(
                {"order_id": order.get("id"), "new_status": "cancel_requested"},
                sort=[("id", -1)],
            )
            revert_status = last_event.get("old_status") if last_event and last_event.get("old_status") else "confirmed"
            return validate_and_apply_order_transition(
                order_id=order_id,
                target_status=revert_status,
                actor_user_id=admin["id"],
                actor_role="admin",
                note=req.admin_note or "Admin rejected cancellation request",
                event_type="cancel_rejected",
            )

        elif action == "complete":
            if curr != "confirmed":
                raise HTTPException(status_code=409, detail="Only confirmed orders can be marked completed.")
            return validate_and_apply_order_transition(
                order_id=order_id,
                target_status="completed",
                actor_user_id=admin["id"],
                actor_role="admin",
                note=req.admin_note,
                event_type="order_completed",
            )

        else:
            raise HTTPException(status_code=400, detail=f"Unknown decision action '{action}'.")

    return await asyncio.to_thread(_decide)


@app.get("/api/admin/orders/{order_id}/events")
async def get_order_audit_events(order_id: int, admin: dict = Depends(require_admin)):
    """Return append-only audit trail for an order from MongoDB."""
    def _query():
        order = mongo_db["orders"].find_one(make_id_query(order_id))
        oid = order.get("id") if order else order_id
        rows = list(mongo_db["order_events"].find({"order_id": oid}).sort("id", 1))
        out = []
        for r in rows:
            d = serialize_doc(r)
            actor_uid = d.get("actor_user_id")
            if actor_uid:
                u = mongo_db["users"].find_one(make_id_query(actor_uid))
                d["actor_email"] = u.get("email") if u else None
                d["actor_name"] = u.get("full_name") if u else None
            else:
                d["actor_email"] = None
                d["actor_name"] = None
            out.append(d)
        return out

    return await asyncio.to_thread(_query)


# =====================================================================
# 19. Compliance & Human Handoff Endpoints (Phase 7)
# =====================================================================
@app.get("/api/admin/handoffs")
async def list_admin_handoffs(
    status_filter: str | None = Query(default=None, alias="status"),
    admin: dict = Depends(require_admin),
):
    """Admin view: List all human handoff requests ordered by priority from MongoDB."""
    def _query():
        query_filter = {}
        if status_filter and status_filter.strip() and status_filter != "all":
            query_filter["status"] = status_filter.strip()

        rows = list(mongo_db["handoff_requests"].find(query_filter))

        # Sort priority: urgent -> high -> normal, status: open -> assigned -> others, id desc
        priority_order = {"urgent": 1, "high": 2, "normal": 3}
        status_order = {"open": 1, "assigned": 2, "resolved": 3}

        def sort_key(doc):
            p = priority_order.get(doc.get("priority", "normal"), 4)
            s = status_order.get(doc.get("status", "open"), 4)
            return (p, s, -int(doc.get("id") or 0))

        rows.sort(key=sort_key)
        out = []
        for r in rows:
            d = serialize_doc(r)
            call_id = d.get("call_id")
            if call_id:
                c = mongo_db["calls"].find_one({"id": call_id})
                d["session_id"] = c.get("session_id") if c else d.get("session_id")
                d["call_customer_name"] = c.get("customer_name") if c else None
                d["call_customer_phone"] = c.get("contact_phone") if c else None
            else:
                d["call_customer_name"] = None
                d["call_customer_phone"] = None

            uid = d.get("user_id")
            if uid:
                u = mongo_db["users"].find_one(make_id_query(uid))
                d["user_email"] = u.get("email") if u else None
            else:
                d["user_email"] = None
            out.append(d)
        return out

    return await asyncio.to_thread(_query)


@app.get("/api/admin/handoffs/summary")
async def get_admin_handoffs_summary(admin: dict = Depends(require_admin)):
    """Return summary counts of open, urgent, and assigned handoffs from MongoDB."""
    def _summary():
        open_c = mongo_db["handoff_requests"].count_documents({"status": "open"})
        urgent_c = mongo_db["handoff_requests"].count_documents({"status": "open", "priority": "urgent"})
        assigned_c = mongo_db["handoff_requests"].count_documents({"status": "assigned"})
        total_c = mongo_db["handoff_requests"].count_documents({})
        return {
            "open_count": open_c,
            "urgent_count": urgent_c,
            "assigned_count": assigned_c,
            "total_count": total_c,
        }

    return await asyncio.to_thread(_summary)


@app.post("/api/admin/handoffs/{handoff_id}/assign")
async def assign_handoff(handoff_id: int, admin: dict = Depends(require_admin)):
    """Assign handoff request to current administrator."""
    def _assign():
        h = mongo_db["handoff_requests"].find_one(make_id_query(handoff_id))
        if not h:
            raise HTTPException(status_code=404, detail="Handoff request not found.")
        now_str = now()
        mongo_db["handoff_requests"].update_one(
            {"_id": h["_id"]},
            {"$set": {
                "assigned_to": admin.get("email"),
                "assigned_at": now_str,
                "status": "assigned",
            }},
        )
        if h.get("call_id"):
            mongo_db["calls"].update_one({"id": h["call_id"]}, {"$set": {"follow_up_status": "pending_staff"}})
        updated = mongo_db["handoff_requests"].find_one({"_id": h["_id"]})
        return serialize_doc(updated)

    return await asyncio.to_thread(_assign)


@app.post("/api/admin/handoffs/{handoff_id}/resolve")
async def resolve_handoff(handoff_id: int, req: HandoffResolveRequest, admin: dict = Depends(require_admin)):
    """Resolve handoff request with staff notes."""
    def _resolve():
        h = mongo_db["handoff_requests"].find_one(make_id_query(handoff_id))
        if not h:
            raise HTTPException(status_code=404, detail="Handoff request not found.")
        now_str = now()
        mongo_db["handoff_requests"].update_one(
            {"_id": h["_id"]},
            {"$set": {
                "status": "resolved",
                "resolved_at": now_str,
                "resolved_by": admin.get("email"),
                "staff_note": req.staff_note,
            }},
        )
        if h.get("call_id"):
            mongo_db["calls"].update_one({"id": h["call_id"]}, {"$set": {"follow_up_status": "closed"}})
        updated = mongo_db["handoff_requests"].find_one({"_id": h["_id"]})
        return serialize_doc(updated)

    return await asyncio.to_thread(_resolve)


@app.post("/api/admin/handoffs/{handoff_id}/priority")
async def update_handoff_priority(handoff_id: int, req: HandoffPriorityRequest, admin: dict = Depends(require_admin)):
    """Update priority level of a handoff request."""
    def _prio():
        h = mongo_db["handoff_requests"].find_one(make_id_query(handoff_id))
        if not h:
            raise HTTPException(status_code=404, detail="Handoff request not found.")
        mongo_db["handoff_requests"].update_one(
            {"_id": h["_id"]},
            {"$set": {"priority": req.priority}},
        )
        updated = mongo_db["handoff_requests"].find_one({"_id": h["_id"]})
        return serialize_doc(updated)

    return await asyncio.to_thread(_prio)


@app.get("/api/calls/{session_id}/handoff-status")
async def get_session_handoff_status(session_id: str):
    """Check customer handoff status for the active call session."""
    def _query():
        h = mongo_db["handoff_requests"].find_one(
            {"session_id": session_id},
            sort=[("id", -1)],
        )
        if not h:
            return {"has_handoff": False, "status": "none"}
        return {
            "has_handoff": True,
            "handoff_id": h.get("id"),
            "trigger_type": h.get("trigger_type"),
            "priority": h.get("priority"),
            "status": h.get("status"),
            "assigned_to": h.get("assigned_to"),
            "created_at": h.get("created_at"),
        }

    return await asyncio.to_thread(_query)


# =====================================================================
# 20. Telephony API Endpoints (Twilio / SIP / PSTN Webhooks & Outbound Calls)
# =====================================================================
TELEPHONY_PROVIDER = os.getenv("TELEPHONY_PROVIDER", "twilio").strip().lower()
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "").strip()
TELEPHONY_WEBHOOK_URL = os.getenv("TELEPHONY_WEBHOOK_URL", "").strip().rstrip("/")
TELEPHONY_DEFAULT_VOICE = os.getenv("TELEPHONY_DEFAULT_VOICE", "Polly.Aditi").strip()
TELEPHONY_LANGUAGE = os.getenv("TELEPHONY_LANGUAGE", "en-US").strip()

try:
    from twilio.rest import Client as TwilioClient
    TWILIO_SDK_AVAILABLE = True
except ImportError:
    TWILIO_SDK_AVAILABLE = False


class OutboundCallRequest(BaseModel):
    to_phone: str
    message: str | None = None
    customer_name: str | None = None


@app.api_route("/api/telephony/voice", methods=["GET", "POST"])
@app.api_route("/api/telephony/inbound", methods=["GET", "POST"])
async def telephony_inbound_voice(request: Request):
    """
    Twilio Voice inbound webhook.
    Generates TwiML XML with <Gather input="speech"> and dynamic business greeting.
    """
    params = dict(request.query_params)
    if request.method == "POST":
        try:
            form = await request.form()
            params.update(dict(form))
        except Exception:
            pass

    call_sid = params.get("CallSid") or f"call_{int(time.time())}"
    caller_phone = params.get("From", "Unknown")
    session_id = f"tel_{call_sid}"

    # Register call session in database
    def _reg():
        call_id = get_or_create_call(session_id, user_id=None)
        mongo_db["calls"].update_one({"id": call_id}, {"$set": {"status": "in-progress"}})
    await asyncio.to_thread(_reg)

    b_name = get_business_setting("business_name", "our business")
    default_greet = f"Hello! Welcome to {b_name} automated telephone assistant. How may I assist you today?"
    greeting = get_business_setting("greeting", default_greet)

    gather_url = f"{TELEPHONY_WEBHOOK_URL}/api/telephony/gather" if TELEPHONY_WEBHOOK_URL else "/api/telephony/gather"

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather input="speech" action="{gather_url}" method="POST" speechTimeout="auto" language="{TELEPHONY_LANGUAGE}">
        <Say voice="{TELEPHONY_DEFAULT_VOICE}" language="{TELEPHONY_LANGUAGE}">{greeting}</Say>
    </Gather>
    <Say voice="{TELEPHONY_DEFAULT_VOICE}" language="{TELEPHONY_LANGUAGE}">We did not receive any input. Please call back soon. Goodbye!</Say>
    <Hangup/>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


@app.post("/api/telephony/gather")
async def telephony_gather_webhook(request: Request):
    """
    Twilio Voice gather webhook: processes caller speech from telephone network,
    queries fast-path / AI reasoning, and returns responsive TwiML.
    """
    form_data = await request.form()
    params = dict(form_data)
    call_sid = params.get("CallSid") or f"call_{int(time.time())}"
    speech_result = (params.get("SpeechResult") or "").strip()
    session_id = f"tel_{call_sid}"

    gather_url = f"{TELEPHONY_WEBHOOK_URL}/api/telephony/gather" if TELEPHONY_WEBHOOK_URL else "/api/telephony/gather"

    if not speech_result:
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather input="speech" action="{gather_url}" method="POST" speechTimeout="auto" language="{TELEPHONY_LANGUAGE}">
        <Say voice="{TELEPHONY_DEFAULT_VOICE}" language="{TELEPHONY_LANGUAGE}">I could not hear your response clearly. Please say your question or order request.</Say>
    </Gather>
    <Say voice="{TELEPHONY_DEFAULT_VOICE}">Thank you for calling. Goodbye!</Say>
    <Hangup/>
</Response>"""
        return Response(content=twiml, media_type="application/xml")

    # Fast-path check or LLM response
    fast_match = check_instant_fast_path(speech_result, session_id, user_id=None)
    if fast_match:
        reply = fast_match["reply"]
        intent = fast_match.get("intent", "general_inquiry")
        outcome = fast_match.get("outcome", "Resolved")
        needs_handoff = fast_match.get("needs_handoff", False)
    else:
        result = await ask_gemini(session_id, speech_result, user_id=None)
        reply = result["reply"]
        intent = result["intent"]
        outcome = result["outcome"]
        needs_handoff = outcome == "Escalated"

    remember(session_id, speech_result, reply)
    await asyncio.to_thread(save_message, session_id, speech_result, {"reply": reply, "intent": intent, "outcome": outcome}, None)

    support_phone = get_business_setting("support_phone", "+923001234567")

    if needs_handoff:
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="{TELEPHONY_DEFAULT_VOICE}">{reply}</Say>
    <Dial timeout="20">{support_phone}</Dial>
</Response>"""
    elif intent == "farewell":
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="{TELEPHONY_DEFAULT_VOICE}">{reply}</Say>
    <Hangup/>
</Response>"""
    else:
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather input="speech" action="{gather_url}" method="POST" speechTimeout="auto" language="{TELEPHONY_LANGUAGE}">
        <Say voice="{TELEPHONY_DEFAULT_VOICE}">{reply}</Say>
    </Gather>
    <Say voice="{TELEPHONY_DEFAULT_VOICE}">Thank you for calling. Have a great day ahead! Goodbye.</Say>
    <Hangup/>
</Response>"""

    return Response(content=twiml, media_type="application/xml")


@app.post("/api/telephony/status")
async def telephony_status_callback(request: Request):
    """Twilio call status callback (ringing, in-progress, completed, failed)."""
    form_data = await request.form()
    params = dict(form_data)
    call_sid = params.get("CallSid")
    call_status = params.get("CallStatus", "completed")

    if call_sid:
        session_id = f"tel_{call_sid}"
        def _update():
            mongo_db["calls"].update_one(
                {"session_id": session_id},
                {"$set": {"status": call_status, "last_activity": now()}},
            )
        await asyncio.to_thread(_update)

    return {"status": "ok", "call_sid": call_sid, "call_status": call_status}


@app.post("/api/telephony/outbound")
async def trigger_outbound_call(req: OutboundCallRequest, admin: dict = Depends(require_admin)):
    """Initiate an automated outbound phone call to a customer using Twilio REST API."""
    to_phone = req.to_phone.strip()
    if not to_phone:
        raise HTTPException(status_code=400, detail="Recipient phone number (to_phone) is required.")

    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_PHONE_NUMBER:
        # Graceful sandbox / mock response when credentials are not yet configured in .env
        simulated_sid = f"SIM_{uuid.uuid4().hex[:16]}"
        return {
            "status": "simulated",
            "provider": TELEPHONY_PROVIDER,
            "call_sid": simulated_sid,
            "to": to_phone,
            "from": TWILIO_PHONE_NUMBER or "+10000000000",
            "message": "Telephony credentials are not configured in .env. Call was simulated for testing.",
        }

    try:
        if not TWILIO_SDK_AVAILABLE:
            raise RuntimeError("twilio Python library is not installed. Install via: pip install twilio")
        client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        webhook_target = f"{TELEPHONY_WEBHOOK_URL}/api/telephony/voice"
        call = client.calls.create(
            to=to_phone,
            from_=TWILIO_PHONE_NUMBER,
            url=webhook_target,
            status_callback=f"{TELEPHONY_WEBHOOK_URL}/api/telephony/status",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
        )
        return {
            "status": "initiated",
            "provider": "twilio",
            "call_sid": call.sid,
            "to": to_phone,
            "from": TWILIO_PHONE_NUMBER,
        }
    except Exception as exc:
        log.error("Outbound telephony call failed: %r", exc)
        raise HTTPException(status_code=500, detail=f"Failed to place outbound call: {exc}")


@app.get("/api/telephony/config")
async def get_telephony_config(admin: dict = Depends(require_admin)):
    """Return telephony configuration status for admin dashboard."""
    is_configured = bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER)
    return {
        "provider": TELEPHONY_PROVIDER,
        "is_configured": is_configured,
        "phone_number": TWILIO_PHONE_NUMBER or None,
        "webhook_url": TELEPHONY_WEBHOOK_URL or None,
        "default_voice": TELEPHONY_DEFAULT_VOICE,
        "language": TELEPHONY_LANGUAGE,
        "inbound_voice_url": f"{TELEPHONY_WEBHOOK_URL}/api/telephony/voice" if TELEPHONY_WEBHOOK_URL else "/api/telephony/voice",
    }


# =====================================================================
# 21. Main Server Entrypoint
# =====================================================================
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
