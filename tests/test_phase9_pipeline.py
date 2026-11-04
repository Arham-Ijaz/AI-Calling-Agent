import os
import sys
import asyncio
import json
import time

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fastapi.testclient import TestClient
from app import app, db, check_instant_fast_path, synthesize_edge_tts, TTS_AUDIO_CACHE, CACHED_FAQ_ANSWERS

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
    assert any(term in full_sentence for term in ["Apex", "assistance", "DigiBuzz", "Hello"])
    assert meta_event["intent"] == "greeting"
    assert meta_event["confidence"] == 1.0
    assert done_event["type"] == "done"
    assert elapsed_ms < 200  # Fast path must be ultra fast (< 200ms)
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

    # Verify handoff table in DB
    with db() as conn:
        row = conn.execute("SELECT * FROM handoff_requests WHERE session_id = 'test_phase9_5'").fetchone()
        assert row is not None
        assert row["trigger_type"] in ("human_agent_request", "user_requested")
    print("[OK] test_human_agent_handoff passed")

def test_direct_handoff_api():
    res = client.post("/api/handoff", json={"session_id": "test_phase9_6", "trigger_type": "low_confidence", "reason": "Latency timeout"})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["handoff_id"] is not None

    with db() as conn:
        row = conn.execute("SELECT * FROM handoff_requests WHERE id = ?", (data["handoff_id"],)).fetchone()
        assert row is not None
        assert row["trigger_type"] == "low_confidence"
    print("[OK] test_direct_handoff_api passed")

def test_call_messages_persistence():
    with db() as conn:
        rows = conn.execute(
            """
            SELECT cm.* FROM call_messages cm
            JOIN calls c ON cm.call_id = c.id
            WHERE c.session_id = 'test_phase9_1'
            """
        ).fetchall()
        assert len(rows) >= 1
        assert rows[0]["user_transcript"] == "hello"
    print("[OK] test_call_messages_persistence passed")

def test_tts_cache():
    async def _run():
        text = "Hello! Welcome to DigiBuzz."
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
    print("\nALL PHASE 9 VERIFICATION TESTS PASSED SUCCESSFULLY!\n")
