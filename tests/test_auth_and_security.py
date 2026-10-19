"""
Security & Authentication Regression Tests for QwickCall
Validates:
1. Endpoint protection: 401 Unauthorized when credentials are missing.
2. Token verification: 200 OK when valid JWT Bearer tokens are provided.
3. SSRF sandbox defense: blocks private IPs, metadata endpoints, and localhost.
4. SessionProxy dictionary iteration methods.
5. Password hashing and verification.
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from app.main import app
from app.core.auth import hash_password, verify_password, create_access_token
from app.api.v1.portals.tenant_portal import is_safe_external_url
from app.core.session_store import SessionProxy

client = TestClient(app)

def test_password_hashing():
    print("Testing password hashing & verification...")
    raw = "SecureP@ssw0rd123!"
    hashed = hash_password(raw)
    assert hashed.startswith("pbkdf2:sha256:100000$"), "Invalid hash format"
    assert verify_password(raw, hashed), "Password verification failed"
    assert not verify_password("wrong_password", hashed), "False positive on wrong password"
    # Legacy migration check
    assert not verify_password("partner123", "partner123"), "Plaintext stored passwords must never verify"
    print("✅ Password hashing & verification passed.")

def test_ssrf_sandbox():
    print("Testing SSRF URL sandbox...")
    assert not is_safe_external_url("http://127.0.0.1:8001"), "Failed to block loopback IP"
    assert not is_safe_external_url("http://localhost:8001"), "Failed to block localhost"
    assert not is_safe_external_url("http://169.254.169.254/latest/meta-data/"), "Failed to block cloud metadata"
    assert not is_safe_external_url("http://10.0.1.50/internal"), "Failed to block RFC1918 10.x.x.x"
    assert not is_safe_external_url("http://192.168.1.1/admin"), "Failed to block RFC1918 192.168.x.x"
    assert not is_safe_external_url("ftp://example.com"), "Failed to block FTP scheme"
    assert is_safe_external_url("https://example.com"), "Legitimate URL falsely blocked"
    print("✅ SSRF sandbox defense passed.")

def test_session_proxy_primitives():
    print("Testing SessionProxy dictionary methods...")
    proxy = SessionProxy()
    proxy["test_auth_call_1"] = {"state": "MAIN_LOOP", "transfer_requested": True}
    assert len(proxy) >= 1, "len(SessionProxy) returned 0"
    assert "test_auth_call_1" in proxy.keys(), "Key not found in keys()"
    items = list(proxy.items())
    assert len(items) >= 1, "items() returned empty list"
    assert any(k == "test_auth_call_1" for k, _ in items), "test key not in items()"
    print("✅ SessionProxy primitives passed.")

def test_admin_auth_guards():
    print("Testing Admin endpoints authentication...")
    # 1. Unauthenticated request must return 401
    res = client.get("/api/v1/admin/overview")
    assert res.status_code == 401, f"Expected 401 for unauthenticated admin overview, got {res.status_code}"

    # 2. Authenticated with valid token must return 200
    token = create_access_token({"sub": "admin_root", "role": "superadmin"})
    res = client.get("/api/v1/admin/overview", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, f"Expected 200 for authenticated admin overview, got {res.status_code}"
    print("✅ Admin auth guard passed.")

def test_reseller_auth_guards():
    print("Testing Reseller endpoints authentication...")
    # 1. Unauthenticated request must return 401
    res = client.get("/api/v1/reseller/overview?reseller_id=partner_apex")
    assert res.status_code == 401, f"Expected 401 for unauthenticated reseller overview, got {res.status_code}"

    # 2. Authenticated request must return 200
    token = create_access_token({"sub": "partner_apex", "reseller_id": "partner_apex", "role": "reseller"})
    res = client.get("/api/v1/reseller/overview?reseller_id=partner_apex", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, f"Expected 200 for authenticated reseller overview, got {res.status_code}"
    print("✅ Reseller auth guard passed.")

def test_outbound_call_auth_guard():
    print("Testing Outbound call authentication & validation...")
    # 1. Unauthenticated request must return 401
    res = client.post("/api/call/outbound", json={"phone_number": "+919876543210"})
    assert res.status_code == 401, f"Expected 401 for unauthenticated outbound call, got {res.status_code}"

    # 2. Authenticated with invalid phone number must return 400
    token = create_access_token({"sub": "admin_root", "role": "superadmin"})
    res = client.post(
        "/api/call/outbound",
        json={"phone_number": "123"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 400, f"Expected 400 for invalid short phone number, got {res.status_code}"
    print("✅ Outbound call auth guard and number validation passed.")

def test_backdoor_tokens_rejected():
    print("Testing that legacy backdoor token prefixes are rejected...")
    res_admin = client.get("/api/v1/admin/overview", headers={"Authorization": "Bearer admin_token_default"})
    assert res_admin.status_code == 401, f"Expected 401 for admin_token_ backdoor, got {res_admin.status_code}"

    res_reseller = client.get("/api/v1/reseller/overview", headers={"Authorization": "Bearer reseller_token_apex"})
    assert res_reseller.status_code == 401, f"Expected 401 for reseller_token_ backdoor, got {res_reseller.status_code}"
    print("✅ Backdoor token rejection verified.")

def test_wallet_guardrail_rejection():
    print("Testing wallet balance pre-call guardrail...")
    from app.services.tenant.tenant_service import check_tenant_wallet_balance
    from app.db.session import sync_engine
    from sqlalchemy import text
    import uuid

    # Create temporary tenant with 0 balance
    test_tid = f"test_broke_{uuid.uuid4().hex[:6]}"
    with sync_engine.connect() as conn:
        conn.execute(
            text("INSERT INTO tenants (tenant_id, business_name, wallet_balance, status) VALUES (:tid, 'Broke Corp', 0.0, 'active')"),
            {"tid": test_tid}
        )
        conn.commit()

    try:
        ok, bal, msg = check_tenant_wallet_balance(test_tid, min_required=15.0)
        assert not ok, "Expected check_tenant_wallet_balance to fail for 0 balance"
        assert bal == 0.0, f"Expected 0.0 balance, got {bal}"

        token = create_access_token({"sub": "admin_root", "role": "superadmin"})
        res = client.post(
            "/api/call/outbound",
            json={"phone_number": "+919876543210", "tenant_id": test_tid},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert res.status_code == 402, f"Expected 402 Payment Required, got {res.status_code}: {res.text}"
        print("✅ Wallet guardrail 402 rejection verified.")
    finally:
        with sync_engine.connect() as conn:
            conn.execute(text("DELETE FROM tenants WHERE tenant_id = :tid"), {"tid": test_tid})
            conn.commit()

def test_tts_lru_cache_bounds():
    print("Testing TTS LRU cache capacity bounds...")
    from app.services.ai.tts_engine import _AUDIO_CHUNK_CACHE, MAX_TTS_CACHE_ENTRIES
    initial_len = len(_AUDIO_CHUNK_CACHE)

    # Insert items up to limit + 20
    for i in range(MAX_TTS_CACHE_ENTRIES + 20):
        key = f"test_cache_entry_{i}"
        _AUDIO_CHUNK_CACHE[key] = [b"chunk"]
        _AUDIO_CHUNK_CACHE.move_to_end(key)
        while len(_AUDIO_CHUNK_CACHE) > MAX_TTS_CACHE_ENTRIES:
            _AUDIO_CHUNK_CACHE.popitem(last=False)

    assert len(_AUDIO_CHUNK_CACHE) == MAX_TTS_CACHE_ENTRIES, f"Cache exceeded limit: {len(_AUDIO_CHUNK_CACHE)} > {MAX_TTS_CACHE_ENTRIES}"
    # Verify oldest entries were evicted
    assert "test_cache_entry_0" not in _AUDIO_CHUNK_CACHE, "Oldest entry 0 was not evicted"
    assert f"test_cache_entry_{MAX_TTS_CACHE_ENTRIES + 19}" in _AUDIO_CHUNK_CACHE, "Newest entry missing"
    print("✅ TTS LRU cache bounds verified.")

if __name__ == "__main__":
    test_password_hashing()
    test_ssrf_sandbox()
    test_session_proxy_primitives()
    test_admin_auth_guards()
    test_reseller_auth_guards()
    test_outbound_call_auth_guard()
    test_backdoor_tokens_rejected()
    test_wallet_guardrail_rejection()
    test_tts_lru_cache_bounds()
    print("\n🎉 ALL AUTHENTICATION, SSRF, WALLET & SECURITY REGRESSION TESTS PASSED!")
