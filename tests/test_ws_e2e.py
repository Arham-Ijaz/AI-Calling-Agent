import os
import sys
import json
import time

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fastapi.testclient import TestClient
import app

def test_websocket_flow():
    client = TestClient(app.app)
    print("Connecting to /ws/ai-call...")
    with client.websocket_connect("/ws/ai-call") as ws:
        # 1. Ping
        ws.send_text(json.dumps({"type": "ping"}))
        pong = ws.receive_json()
        assert pong.get("type") == "pong"

        queries = [
            "What are your delivery times?",
            "What is the minimum order quantity for wholesale prices?",
        ]

        for idx, q in enumerate(queries, 1):
            print(f"\n--- Turn {idx}: '{q}' ---")
            t0 = time.time()
            ws.send_text(json.dumps({
                "type": "message",
                "session_id": "test-multiturn-session",
                "text": q
            }))

            text_chunks = 0
            binary_chunks = 0
            first_audio_time = None
            first_text_time = None

            while True:
                msg = ws.receive()
                if "text" in msg:
                    data = json.loads(msg["text"])
                    mtype = data.get("type")
                    if mtype == "filler":
                        print(f"  [0ms Filler]: '{data.get('text')}'")
                    elif mtype == "text_delta":
                        if first_text_time is None:
                            first_text_time = time.time()
                        text_chunks += 1
                    elif mtype == "done":
                        t_done = time.time()
                        print(f"  [DONE] Total Turn Time: {t_done - t0:.3f}s")
                        print(f"  First audio latency: {(first_audio_time - t0)*1000:.1f}ms" if first_audio_time else "No audio")
                        print(f"  Total text tokens: {text_chunks}")
                        print(f"  Total audio chunks: {binary_chunks}")
                        print(f"  Reply: {data.get('reply')[:70]}...")
                        break
                elif "bytes" in msg:
                    if first_audio_time is None:
                        first_audio_time = time.time()
                    binary_chunks += 1

            assert binary_chunks >= 2, "Expected filler audio + reply audio"
            assert text_chunks > 0, "Expected streamed text tokens"

    print("\nAll multi-turn streaming tests PASSED!")

if __name__ == "__main__":
    test_websocket_flow()
