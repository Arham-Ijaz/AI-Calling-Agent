import json
from fastapi.testclient import TestClient
import app

def test_websocket_flow():
    client = TestClient(app.app)
    print("Connecting to /ws/ai-call...")
    with client.websocket_connect("/ws/ai-call") as ws:
        # 1. Test ping/pong
        ws.send_text(json.dumps({"type": "ping"}))
        pong = ws.receive_json()
        print("Received ping response:", pong)
        assert pong.get("type") == "pong"

        # 2. Test sending a message
        print("Sending message...")
        ws.send_text(json.dumps({
            "type": "message",
            "session_id": "test-session-ws-1",
            "text": "Hello, what are your delivery times?"
        }))

        # 3. Receive streaming frames
        text_frames = []
        binary_frames = []
        is_done = False

        for _ in range(50):
            msg = ws.receive()
            if "text" in msg:
                data = json.loads(msg["text"])
                msg_type = data.get("type")
                if msg_type == "start":
                    print("[START frame received]")
                elif msg_type == "text_delta":
                    text_frames.append(data.get("delta", ""))
                elif msg_type == "audio_start":
                    print("[AUDIO_START frame received]")
                elif msg_type == "audio_end":
                    print(f"[AUDIO_END frame received]")
                elif msg_type == "done":
                    print(f"[DONE frame received] Reply: {data.get('reply')[:60]}... Intent: {data.get('intent')}")
                    is_done = True
                    break
                elif msg_type == "error":
                    print("[ERROR frame received]:", data)
                    break
            elif "bytes" in msg:
                binary_frames.append(msg["bytes"])
                print(f"[BINARY audio chunk received]: {len(msg['bytes'])} bytes")

        print(f"Total text delta chunks: {len(text_frames)}")
        print(f"Total binary audio chunks: {len(binary_frames)}")
        total_audio_bytes = sum(len(b) for b in binary_frames)
        print(f"Total audio bytes streamed: {total_audio_bytes} bytes")

        assert is_done, "Expected 'done' frame"
        assert len(binary_frames) > 0, "Expected at least one binary audio chunk"
        assert total_audio_bytes > 500, "Expected valid audio byte payload"
        print("WebSocket live voice streaming test PASSED successfully!")

if __name__ == "__main__":
    test_websocket_flow()
