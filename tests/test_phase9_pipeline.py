import os
import sys
import asyncio
import json
import time

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fastapi.testclient import TestClient
from app import (
    app,
    mongo_db,
    db,
    check_instant_fast_path,
    synthesize_edge_tts,
    TTS_AUDIO_CACHE,
    CACHED_FAQ_ANSWERS,
    create_access_token,
    COOKIE_NAME,
    make_id_query,
    load_faqs_context,
)

client = TestClient(app)

def test_fillers_endpoint():
    res = client.get("/api/fillers")
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    data = res.json()
    assert "en" in data and "ur" in data and "roman_ur" in data
    assert "short_ack" in data["en"]
    assert "order_status" in data["en"]
    print("[OK] test_fillers_endpoint passed")

def parse_sse_events(raw_text: str):
    events = []
    for line in raw_text.split("\n"):
        line = line.strip()
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload:
                events.append(json.loads(payload))
    return events

def test_instant_fast_path_greetings():
    t0 = time.perf_counter()
    res = client.post("/api/chat/stream", json={"session_id": "test_phase9_1", "message": "hello"})
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert res.status_code == 200
    assert "text/event-stream" in res.headers["content-type"]
    events = parse_sse_events(res.text)
    assert len(events) >= 3  # at least 1 sentence, 1 meta, 1 done
    sentence_events = [e for e in events if e.get("type") == "sentence"]
    meta_event = next(e for e in events if e.get("type") == "meta")
    done_event = next(e for e in events if e.get("type") == "done")

    assert len(sentence_events) >= 1
    full_sentence = " ".join(s["text"] for s in sentence_events)
    assert any(term in full_sentence for term in ["Apex", "assistance", "DigiBuzz", "Hello", "AI", "Calling", "Agent"])
    assert meta_event["intent"] == "greeting"
    assert meta_event["confidence"] == 1.0
    assert done_event["type"] == "done"
    assert elapsed_ms < 1500  # Fast path with MongoDB Atlas cloud network latency (< 1500ms)
    print(f"[OK] test_instant_fast_path_greetings passed in {elapsed_ms:.1f}ms")

def test_instant_fast_path_order_lookup():
    res = client.post("/api/chat/stream", json={"session_id": "test_phase9_2", "message": "What is status of order ORD-1001?"})
    assert res.status_code == 200
    events = parse_sse_events(res.text)
    full_text = " ".join(e["text"] for e in events if e.get("type") == "sentence")
    meta = next(e for e in events if e.get("type") == "meta")
    assert "ORD-1001" in full_text
    assert any(term in full_text for term in ["confirmation", "received", "status", "Shipped"])
    assert meta["intent"] == "order_status"
    print("[OK] test_instant_fast_path_order_lookup passed")

def test_instant_fast_path_business_hours():
    res = client.post("/api/chat/stream", json={"session_id": "test_phase9_3", "message": "What are your business hours?"})
    assert res.status_code == 200
    events = parse_sse_events(res.text)
    full_text = " ".join(e["text"] for e in events if e.get("type") == "sentence")
    meta = next(e for e in events if e.get("type") == "meta")
    assert "Monday" in full_text and "Saturday" in full_text
    assert meta["intent"] == "business_hours"
    print("[OK] test_instant_fast_path_business_hours passed")

def test_instant_fast_path_quantity_verification():
    res = client.post("/api/chat/stream", json={"session_id": "test_phase9_4", "message": "5 pieces"})
    assert res.status_code == 200
    events = parse_sse_events(res.text)
    full_text = " ".join(e["text"] for e in events if e.get("type") == "sentence")
    meta = next(e for e in events if e.get("type") == "meta")
    assert "5 units" in full_text
    assert meta["intent"] == "quantity_verification"
    assert meta["quantity"] == 5
    print("[OK] test_instant_fast_path_quantity_verification passed")

def test_human_agent_handoff():
    res = client.post("/api/chat/stream", json={"session_id": "test_phase9_5", "message": "I want to speak with a human agent"})
    assert res.status_code == 200
    events = parse_sse_events(res.text)
    meta = next(e for e in events if e.get("type") == "meta")
    assert meta["intent"] in ("human_agent_request", "human_handoff")
    assert meta["needs_handoff"] is True
    assert meta["outcome"] in ("Transferred", "Escalated")

    # Verify handoff table in MongoDB
    row = mongo_db["handoff_requests"].find_one({"session_id": "test_phase9_5"})
    assert row is not None
    assert row["trigger_type"] in ("human_agent_request", "user_requested")
    print("[OK] test_human_agent_handoff passed")

def test_direct_handoff_api():
    res = client.post("/api/handoff", json={"session_id": "test_phase9_6", "trigger_type": "low_confidence", "reason": "Latency timeout"})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["handoff_id"] is not None

    row = mongo_db["handoff_requests"].find_one({"id": data["handoff_id"]})
    assert row is not None
    assert row["trigger_type"] == "low_confidence"
    print("[OK] test_direct_handoff_api passed")

def test_call_messages_persistence():
    call = mongo_db["calls"].find_one({"session_id": "test_phase9_1"})
    assert call is not None
    rows = list(mongo_db["call_messages"].find({"call_id": call["id"]}))
    assert len(rows) >= 1
    assert rows[0]["user_transcript"] == "hello"
    print("[OK] test_call_messages_persistence passed")

def test_tts_cache():
    async def _run():
        text = "Hello! Welcome to our automated assistance."
        # First call synthesizes
        audio1 = await synthesize_edge_tts(text)
        assert len(audio1) > 0
        # Second call should be instant from memory cache
        t0 = time.perf_counter()
        audio2 = await synthesize_edge_tts(text)
        elapsed_us = (time.perf_counter() - t0) * 1_000_000
        assert audio1 == audio2
        assert elapsed_us < 10000  # < 10ms cache hit
        print(f"[OK] test_tts_cache passed in {elapsed_us:.0f} microseconds")

    asyncio.run(_run())

def test_unauthenticated_call_logs_protection():
    """Verify that unauthenticated guest users are strictly rejected with 401 on call logs."""
    # 1. GET /api/calls without session cookie
    res = client.get("/api/calls")
    assert res.status_code == 401, f"Expected 401 Unauthorized for guest call logs, got {res.status_code}"

    # 2. GET /api/history without session cookie
    res_hist = client.get("/api/history?session_id=test_phase9_1")
    assert res_hist.status_code == 401, f"Expected 401 Unauthorized for guest transcript, got {res_hist.status_code}"

    # 3. POST /api/calls/sync without session cookie
    res_sync = client.post("/api/calls/sync", json={"session_id": "test_sync", "messages": []})
    assert res_sync.status_code == 401, f"Expected 401 Unauthorized for guest sync, got {res_sync.status_code}"
    print("[OK] test_unauthenticated_call_logs_protection passed (401 enforced)")

def test_guest_quota_enforcement():
    """Verify that guest quota enforces max 3 interactions before triggering mandatory auth barrier."""
    guest_sid = f"guest_quota_session_{int(time.time())}"
    # Turn 1
    r1 = client.post("/api/chat/stream", json={"session_id": guest_sid, "message": "hello"})
    assert r1.status_code == 200
    events1 = parse_sse_events(r1.text)
    assert not any(e.get("type") == "guest_limit" for e in events1)

    # Turn 2
    r2 = client.post("/api/chat/stream", json={"session_id": guest_sid, "message": "what is your working hours?"})
    assert r2.status_code == 200
    events2 = parse_sse_events(r2.text)
    assert not any(e.get("type") == "guest_limit" for e in events2)

    # Turn 3
    r3 = client.post("/api/chat/stream", json={"session_id": guest_sid, "message": "do you sell laptops?"})
    assert r3.status_code == 200
    events3 = parse_sse_events(r3.text)
    assert not any(e.get("type") == "guest_limit" for e in events3)

    # Turn 4 (Exceeds GUEST_CALL_LIMIT=3) -> Guest limit reached
    r4 = client.post("/api/chat/stream", json={"session_id": guest_sid, "message": "how much is dell laptop?"})
    assert r4.status_code == 200
    events4 = parse_sse_events(r4.text)
    assert any(e.get("type") == "guest_limit" or e.get("intent") == "guest_limit_reached" for e in events4)
    print("[OK] test_guest_quota_enforcement passed (quota wall triggered after 3 turns)")

def test_dynamic_branding_public():
    """Verify GET /api/business-settings is public and dynamic branding reflects immediately."""
    res = client.get("/api/business-settings")
    assert res.status_code == 200
    settings = res.json()
    assert "business_name" in settings
    assert len(settings["business_name"]) > 0
    print(f"[OK] test_dynamic_branding_public passed (business_name: '{settings['business_name']}')")

def test_authenticated_admin_bypasses_guest_quota():
    """Verify that authenticated users (especially Admin) completely bypass the 3-interaction guest limit."""
    admin_row = mongo_db["users"].find_one({"role": "admin"})
    assert admin_row is not None, "Admin user must exist in MongoDB"
    admin_token = create_access_token(admin_row.get("id"), "admin", admin_row.get("email"))

    session_id = f"admin_bypass_session_{int(time.time())}"
    # Set the cookie on TestClient
    client.cookies.set(COOKIE_NAME, admin_token)

    # Perform 5 interactions (well over the guest limit of 3)
    queries = ["hello", "working hours", "laptop price", "refund policy", "thank you"]
    for idx, q in enumerate(queries, 1):
        res = client.post("/api/chat/stream", json={"session_id": session_id, "message": q})
        assert res.status_code == 200
        events = parse_sse_events(res.text)
        assert not any(e.get("type") == "guest_limit" for e in events), f"Admin incorrectly blocked at turn {idx}!"
        assert not any(e.get("intent") == "guest_limit_reached" for e in events), f"Admin triggered guest_limit_reached at turn {idx}!"

    # Clean up cookies for subsequent tests
    client.cookies.clear()
    print("[OK] test_authenticated_admin_bypasses_guest_quota passed (Admin bypassed guest quota on 5 turns)")

def test_nosql_injection_protection():
    """Verify make_id_query safely rejects MongoDB operator injection attempts."""
    # Dict injection
    q1 = make_id_query({"$gt": ""})
    assert q1 == {"id": None}

    # Dollar prefix injection
    q2 = make_id_query("$where")
    assert q2 == {"id": None}

    # Null byte injection
    q3 = make_id_query("admin\0inject")
    assert q3 == {"id": None}

    # Valid string and int still work
    q4 = make_id_query(42)
    assert q4.get("id") == 42 or "$or" in q4
    print("[OK] test_nosql_injection_protection passed (operator injections safely rejected)")

def test_faq_optimization_latency_compression():
    """Verify FAQs are compressed and relevance-chunked to minimize token overhead."""
    # Test without query_text: returns compact bullet summaries
    ctx_general = load_faqs_context(query_text=None, max_faqs=5)
    assert "• [" in ctx_general
    assert "→" in ctx_general
    # Verify it does not dump all 24 verbose items
    assert ctx_general.count("• [") <= 5

    # Test with query_text: returns targeted top-matching FAQ
    ctx_delivery = load_faqs_context(query_text="How long does delivery take to Lahore?", max_faqs=3)
    assert "delivery" in ctx_delivery.lower()
    print("[OK] test_faq_optimization_latency_compression passed (compact high-speed prompt context)")

if __name__ == "__main__":
    print("\n--- Running Phase 9 Automated Verification ---")
    test_fillers_endpoint()
    test_instant_fast_path_greetings()
    test_instant_fast_path_order_lookup()
    test_instant_fast_path_business_hours()
    test_instant_fast_path_quantity_verification()
    test_human_agent_handoff()
    test_direct_handoff_api()
    test_call_messages_persistence()
    test_tts_cache()
    test_unauthenticated_call_logs_protection()
    test_guest_quota_enforcement()
    test_dynamic_branding_public()
    test_authenticated_admin_bypasses_guest_quota()
    test_nosql_injection_protection()
    test_faq_optimization_latency_compression()
    print("\nALL VERIFICATION TESTS PASSED SUCCESSFULLY!\n")
