"""
DigiBuzz AI Calling Agent — Comprehensive Quality Assurance & Responsive Verification Test
Tests CSS syntax, HTML IDs, responsive media queries, anti-blowout rules, and backend APIs.
"""
import sys
import re
from bs4 import BeautifulSoup

def test_css_syntax():
    print("[RUNNING] Testing CSS syntax & balanced braces in static/main.css...")
    with open("static/main.css", "r", encoding="utf-8") as f:
        content = f.read()

    # Check balanced braces
    # Remove strings and comments first
    clean = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
    clean = re.sub(r'url\(.*?\)', '', clean)
    clean = re.sub(r'".*?"', '', clean)
    clean = re.sub(r"'.*?'", '', clean)

    open_braces = clean.count("{")
    close_braces = clean.count("}")
    assert open_braces == close_braces, f"Mismatched braces in CSS: {open_braces} open vs {close_braces} close"
    print(f"[PASS] CSS braces balanced: {open_braces} open, {close_braces} close.")

    # Check key responsive breakpoints
    required_queries = [
        "max-width: 1024px",
        "max-width: 992px",
        "max-width: 768px",
        "max-width: 600px",
        "max-width: 380px"
    ]
    for q in required_queries:
        assert q in content, f"Missing required media query: {q}"
        print(f"[PASS] Found responsive breakpoint: @media ({q})")

    # Check iOS auto-zoom prevention
    assert "16px !important" in content, "Missing iOS auto-zoom prevention (font-size: 16px !important)"
    print("[PASS] iOS auto-zoom prevention rules verified.")

    # Check anti-blowout rules
    assert "overflow-x: hidden" in content, "Missing overflow-x: hidden on root containers"
    assert "min-width: 0" in content, "Missing min-width: 0 constraints on flex/grid items"
    print("[PASS] Anti-blowout overflow-x and min-width: 0 verified.")

    # Check native bottom sheet modal animation
    assert "sheetSlideUp" in content, "Missing sheetSlideUp animation for mobile bottom sheet modals"
    print("[PASS] Mobile bottom sheet animation verified.")


def test_html_ids_and_structure():
    print("\n[RUNNING] Testing HTML DOM structure & IDs in index.html...")
    with open("index.html", "r", encoding="utf-8") as f:
        html = f.read()

    soup = BeautifulSoup(html, "html.parser")

    # Check meta viewport
    viewport = soup.find("meta", attrs={"name": "viewport"})
    assert viewport is not None, "Missing <meta name='viewport'>"
    assert "width=device-width" in viewport.get("content", ""), "Invalid viewport width"
    print("[PASS] <meta name='viewport'> configured correctly.")

    # Critical DOM IDs required by JS modules
    critical_ids = [
        "navLinks", "navHamburger", "keyStatusBadge",
        "themeToggleBtn", "themeToggleIcon", "themeToggleText",
        "authNavGuest", "authNavUser", "btnOpenLogin", "btnLogout",
        "mobileAuthGuest", "mobileAuthUser", "mobileBtnLogin", "mobileBtnLogout",
        "page-home", "page-faqs", "page-products", "page-logs", "page-admin", "page-settings",
        "chatWindow", "chatInput", "btnSend", "btnMic", "btnStartCall", "btnEndCall", "btnNewSession",
        "statusIndicator", "statusDot", "statusText", "callHintBanner", "chipsSection", "chipScrollLeft", "chipScrollRight",
        "sideSessionId", "sideStatus", "sideTurnCount", "sideIntent", "sideOutcome", "sideVoiceStatus",
        "faqSearch", "faqIntentFilter", "faqCount", "faqTableBody", "btnAddFaq", "btnRefreshFaqs",
        "productSearch", "productCategoryFilter", "productCount", "productTableBody", "btnAddProduct", "btnRefreshProducts",
        "logsTableBody", "btnRefreshLogs", "statTotal", "statResolved", "statEscalated", "statMessages",
        "adminUserSearch", "adminRoleFilter", "adminStatusFilter", "adminUsersCount", "adminUsersTableBody", "btnRefreshAdmin",
        "settingsUserName", "settingsUserEmail", "settingsUserPhone", "settingsUserRoleBadge",
        "settingsSessionId", "settingsCallStatus", "settingsVoiceStatus", "settingsBtnToggleVoice", "settingsBtnNewSession",
        "settingsAdminSection", "btnRefreshKeyStatus", "authModal", "faqModal", "productModal", "transcriptModal"
    ]

    for elem_id in critical_ids:
        elem = soup.find(id=elem_id)
        assert elem is not None, f"Required DOM element with id='{elem_id}' not found in index.html!"
    print(f"[PASS] All {len(critical_ids)} critical DOM IDs present and accounted for.")


def test_api_endpoints():
    print("\n[RUNNING] Testing backend APIs and RBAC Security with FastAPI TestClient...")
    from starlette.testclient import TestClient
    from app import app, init_db

    init_db()
    client = TestClient(app)

    # 1. Health check / Key status (Guest -> should fail with 401 or 403 on restricted)
    res = client.get("/api/key-status")
    assert res.status_code in [401, 403], f"Unauthenticated user should not access key-status, got {res.status_code}"
    print("[PASS] Unauthenticated access to /api/key-status properly blocked (RBAC enforced).")

    # 2. Public FAQs
    res = client.get("/api/faqs")
    assert res.status_code == 200, f"Expected 200 from /api/faqs, got {res.status_code}"
    faqs_data = res.json()
    assert "questions" in faqs_data, "Invalid faqs response: expected key 'questions'"
    print(f"[PASS] /api/faqs working correctly (returned {len(faqs_data['questions'])} FAQs).")

    # 3. Public Products
    res = client.get("/api/products")
    assert res.status_code == 200, f"Expected 200 from /api/products, got {res.status_code}"
    products = res.json()
    assert isinstance(products, list), "Invalid products response: expected list"
    print(f"[PASS] /api/products working correctly (returned {len(products)} products).")

    # 4. Auth Me as guest
    res = client.get("/api/auth/me")
    assert res.status_code == 401, f"Expected 401 for guest /api/auth/me, got {res.status_code}"
    print("[PASS] /api/auth/me correctly returns 401 for guest.")

    # 5. Admin Login
    res = client.post("/api/auth/login", json={"identifier": "admin@digibuzz.com", "password": "Admin@12345"})
    assert res.status_code == 200, f"Admin login failed: {res.text}"
    admin_data = res.json()
    assert admin_data["user"]["role"] == "admin"
    print("[PASS] Admin login successful with role 'admin'.")

    # 6. Admin key-status with authenticated cookies
    res = client.get("/api/key-status")
    assert res.status_code == 200, f"Admin should access key-status, got {res.status_code}"
    status_data = res.json()
    assert "active_key" in status_data
    print("[PASS] Admin successfully accessed /api/key-status diagnostics.")

    # 7. Admin Metrics
    res = client.get("/api/admin/metrics")
    assert res.status_code == 200, f"Admin should access metrics, got {res.status_code}"
    print("[PASS] Admin successfully accessed /api/admin/metrics.")

    # 8. Regular user registration & restricted access test
    import random
    test_email = f"user_{random.randint(1000, 9999)}@digibuzz.test"
    test_phone = f"+92300{random.randint(1000000, 9999999)}"
    reg_res = client.post("/api/auth/register", json={
        "full_name": "Test Member",
        "email": test_email,
        "phone": test_phone,
        "password": "Password123"
    })
    assert reg_res.status_code in [200, 201], f"Registration failed: {reg_res.text}"
    user_data = reg_res.json()
    assert user_data["user"]["role"] == "user"
    print(f"[PASS] Member registration successful ({test_email}).")

    # Test member attempting admin metrics -> must get 403 Forbidden!
    forbidden_res = client.get("/api/admin/metrics")
    assert forbidden_res.status_code == 403, f"Member must get 403 Forbidden on /api/admin/metrics, got {forbidden_res.status_code}"
    print("[PASS] OWASP Top 10 A01 RBAC verified: Regular member receives 403 Forbidden on /api/admin/metrics.")

    # 9. Voice Call Turn sync
    call_sync_res = client.post("/api/calls/sync", json={
        "session_id": "test_session_12345",
        "status": "active",
        "outcome": "Resolved",
        "total_messages": 1,
        "messages": [{
            "timestamp": "2026-10-31 01:00:00",
            "user_transcript": "Hello, testing call sync",
            "ai_response": "Welcome to DigiBuzz wholesale support!",
            "detected_intent": "greeting",
            "outcome": "Resolved"
        }]
    })
    assert call_sync_res.status_code == 200, f"Call sync failed: {call_sync_res.text}"
    print("[PASS] Call session turn synced successfully.")

    # 10. Voice TTS test
    tts_res = client.post("/api/tts", json={"text": "Hello wholesale test"})
    assert tts_res.status_code == 200, f"TTS failed: {tts_res.text}"
    assert tts_res.headers.get("content-type", "").startswith("audio/")
    print(f"[PASS] Voice synthesis generated valid audio payload ({len(tts_res.content)} bytes).")

if __name__ == "__main__":
    test_css_syntax()
    test_html_ids_and_structure()
    test_api_endpoints()
    print("\n=======================================================")
    print("[SUCCESS] ALL QUALITY ASSURANCE & RESPONSIVE TESTS PASSED 100%!")
    print("=======================================================")
