"""
Security hardening regression tests (tenant isolation, auth bypasses, toll fraud, webhooks).

These tests write fixture rows, so they only run against a local throwaway database:
    DATABASE_URL=postgresql+asyncpg://postgres:pw@127.0.0.1:5432/qwickcall_test \
    SYNC_DATABASE_URL=postgresql://postgres:pw@127.0.0.1:5432/qwickcall_test \
    REDIS_URL= pytest tests/test_security_hardening.py
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_DB_URL = os.getenv("SYNC_DATABASE_URL", "")
if not any(h in _DB_URL for h in ("@127.0.0.1", "@localhost")):
    pytest.skip("security hardening tests require a local SYNC_DATABASE_URL (they write fixture rows)", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db.init_db import init_database  # noqa: E402
from app.db.session import sync_engine  # noqa: E402
from app.core.auth import hash_password, create_access_token  # noqa: E402
from app.core.secrets_box import encrypt_secret, decrypt_secret  # noqa: E402
from app.services.tenant.tenant_service import check_tenant_wallet_balance  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)
RUN = uuid.uuid4().hex[:6]
TENANT_A = f"t_a_{RUN}"
TENANT_B = f"t_b_{RUN}"
RESELLER = f"res_{RUN}"
CLIENT_OF_RESELLER = f"cl_sub_{RUN}"
STRONG_PW = "Str0ng-Test-Passw0rd!"


def _bearer(payload):
    return {"Authorization": f"Bearer {create_access_token(payload)}"}


TOKEN_A = _bearer({"sub": TENANT_A, "tenant_id": TENANT_A, "role": "tenant"})
TOKEN_B = _bearer({"sub": TENANT_B, "tenant_id": TENANT_B, "role": "tenant"})
TOKEN_ADMIN = _bearer({"sub": "admin_root", "role": "superadmin"})
TOKEN_RESELLER = _bearer({"sub": RESELLER, "reseller_id": RESELLER, "role": "reseller"})


@pytest.fixture(scope="module", autouse=True)
def fixtures():
    init_database()
    with sync_engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO resellers (reseller_id, name, company_name, email, password_hash, status, wholesale_rate_per_min, wallet_balance)
            VALUES (:rid, 'R', 'Reseller Co', :email, :pwd, 'active', 3.0, 100.0)
        """), {"rid": RESELLER, "email": f"{RESELLER}@example.com", "pwd": hash_password("partner123")})
        for tid, name, rid, pwd in (
            (TENANT_A, f"Acme {RUN}", None, hash_password(STRONG_PW)),
            (TENANT_B, f"Victim {RUN}", None, hash_password("tenant123")),
            (CLIENT_OF_RESELLER, f"Sub {RUN}", RESELLER, hash_password(STRONG_PW)),
        ):
            conn.execute(text("""
                INSERT INTO tenants (tenant_id, business_name, email, password_hash, status, reseller_id, rate_per_min, wallet_balance)
                VALUES (:tid, :name, :email, :pwd, 'active', :rid, 5.0, 500.0)
            """), {"tid": tid, "name": name, "email": f"{tid}@example.com", "pwd": pwd, "rid": rid})
        conn.execute(text("INSERT INTO tenant_phone_numbers (number, tenant_id, vendor, is_active) VALUES (:n, :t, 'rpdigital', true)"),
                     {"n": f"+9180{RUN.encode().hex()[:8]}", "t": TENANT_B})
        conn.execute(text("INSERT INTO leads (lead_id, tenant_id, name, phone, reason, status) VALUES (:l, :t, 'Victim Lead', '+919999999999', 'secret', 'hot')"),
                     {"l": f"lead_{RUN}", "t": TENANT_B})
        conn.execute(text("""
            INSERT INTO agents (agent_id, tenant_id, name, persona, greeting, is_active) VALUES
            (:a1, :tb, 'B1', 'p', 'g', true), (:a2, :tb, 'B2', 'p', 'g', true)
        """), {"a1": f"ag_b1_{RUN}", "a2": f"ag_b2_{RUN}", "tb": TENANT_B})
        conn.commit()
    yield


# --- Public surface ---------------------------------------------------------
def test_health_exposes_no_configuration():
    body = client.get("/health").json()
    assert set(body) == {"status", "version"}


def test_detailed_health_requires_admin():
    assert client.get("/api/v1/admin/system-health").status_code == 401
    assert client.get("/api/v1/admin/system-health", headers=TOKEN_ADMIN).status_code == 200


# --- Authentication bypasses -------------------------------------------------
def test_tenant_id_header_or_query_is_not_authentication():
    assert client.get(f"/api/v1/tenant/details/{TENANT_B}", headers={"X-Tenant-ID": TENANT_B}).status_code == 401
    assert client.get(f"/api/v1/tenant/details/{TENANT_B}?tenant_id={TENANT_B}").status_code == 401
    assert client.get("/api/v1/tenant/leads", headers={"X-Tenant-ID": "%"}).status_code == 401


def test_admin_key_header_is_not_authentication():
    assert client.get("/api/v1/admin/overview", headers={"X-Admin-Key": "admin123"}).status_code == 401


def test_default_and_empty_passwords_rejected():
    assert client.post("/api/v1/tenant/login", json={"username": TENANT_B, "password": "tenant123"}).status_code == 401
    assert client.post("/api/v1/tenant/login", json={"username": TENANT_B, "password": ""}).status_code in (400, 401)
    assert client.post("/api/v1/tenant/login", json={"username": "%", "password": STRONG_PW}).status_code == 401
    assert client.post("/api/v1/admin/login", json={"email": "admin@qwickcall.ai", "password": "admin123"}).status_code == 401
    assert client.post("/api/v1/reseller/login", json={"email": f"{RESELLER}@example.com", "password": "partner123"}).status_code == 401
    ok = client.post("/api/v1/tenant/login", json={"username": TENANT_A, "password": STRONG_PW})
    assert ok.status_code == 200 and ok.json()["token"]


def test_register_cannot_take_over_existing_account_or_did():
    dup = client.post("/api/v1/tenant/register", json={
        "business_name": "Evil", "email": f"{TENANT_B}@example.com", "password": STRONG_PW})
    assert dup.status_code == 409 and "api_key" not in dup.json()
    with sync_engine.connect() as conn:
        victim_did = conn.execute(text("SELECT number FROM tenant_phone_numbers WHERE tenant_id = :t"), {"t": TENANT_B}).scalar()
    steal = client.post("/api/v1/tenant/register", json={
        "business_name": "Evil", "email": f"evil_{RUN}@example.com", "password": STRONG_PW, "did_number": victim_did})
    assert steal.status_code == 409
    weak = client.post("/api/v1/tenant/register", json={
        "business_name": "Evil", "email": f"weak_{RUN}@example.com", "password": "tenant123"})
    assert weak.status_code == 400


# --- Cross-tenant isolation (IDOR) -------------------------------------------
@pytest.mark.parametrize("path,payload", [
    ("/api/v1/tenant/messaging-settings", {"sender_email": "attacker@evil.test", "smtp_host": "smtp.evil.test"}),
    ("/api/v1/tenant/telephony-settings", {"telephony_mode": "byoc", "exotel_api_token": "x"}),
    ("/api/v1/tenant/scrape-website", {"website_url": "https://example.com"}),
    ("/api/v1/tenant/leads", {"phone": "+911234567890"}),
    ("/api/v1/tenant/unassign-number", {"phone_number": "+910000000000"}),
])
def test_tenant_cannot_modify_other_tenant(path, payload):
    res = client.post(path, json={"tenant_id": TENANT_B, **payload}, headers=TOKEN_A)
    assert res.status_code == 403, res.text


def test_tenant_cannot_upload_into_other_tenant():
    pdf = b"%PDF-1.4\n" + b"0" * 200
    res = client.post("/api/v1/tenant/upload-brochure", data={"tenant_id": TENANT_B},
                      files={"file": ("b.pdf", pdf, "application/pdf")}, headers=TOKEN_A)
    assert res.status_code == 403


def test_brochure_path_traversal_rejected():
    pdf = b"%PDF-1.4\n" + b"0" * 200
    res = client.post("/api/v1/tenant/upload-brochure", data={"tenant_id": "../../app/evil"},
                      files={"file": ("b.pdf", pdf, "application/pdf")}, headers=TOKEN_ADMIN)
    assert res.status_code == 400


def test_lead_status_and_agent_delete_scoped():
    assert client.post(f"/api/v1/tenant/leads/lead_{RUN}/status", json={"status": "cold"}, headers=TOKEN_A).status_code == 404
    assert client.delete(f"/api/v1/tenant/{TENANT_A}/agents/ag_b1_{RUN}", headers=TOKEN_A).status_code in (400, 404)
    with sync_engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM agents WHERE agent_id = :a"), {"a": f"ag_b1_{RUN}"}).scalar() == 1


def test_business_name_clone_does_not_leak_leads():
    clone_id = f"t_clone_{RUN}"
    with sync_engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO tenants (tenant_id, business_name, email, password_hash, status)
            VALUES (:tid, :name, :email, :pwd, 'active')
        """), {"tid": clone_id, "name": f"victim {RUN}", "email": f"{clone_id}@example.com", "pwd": hash_password(STRONG_PW)})
        conn.commit()
    clone_token = _bearer({"sub": clone_id, "tenant_id": clone_id, "role": "tenant"})
    res = client.get(f"/api/v1/tenant/leads?tenant_id={clone_id}", headers=clone_token)
    assert res.status_code == 200
    assert all(lead["name"] != "Victim Lead" for lead in res.json()["leads"])
    analytics = client.get(f"/api/analytics/overview?tenant_id={clone_id}", headers=clone_token).json()
    assert analytics["metrics"]["total_leads"] == 0


def test_assign_number_cannot_steal_bound_did():
    with sync_engine.connect() as conn:
        victim_did = conn.execute(text("SELECT number FROM tenant_phone_numbers WHERE tenant_id = :t"), {"t": TENANT_B}).scalar()
    res = client.post("/api/v1/tenant/assign-number", json={"tenant_id": TENANT_A, "phone_number": victim_did}, headers=TOKEN_A)
    assert res.status_code == 409


# --- Toll fraud / cost abuse --------------------------------------------------
def test_outbound_call_requires_auth_and_plain_number():
    assert client.post("/api/call/outbound", json={"phone_number": "+919876543210"}).status_code == 401
    assert client.post("/api/call/outbound", json={"phone_number": "*99#"}, headers=TOKEN_A).status_code == 400
    assert client.post("/api/call/outbound", json={"phone_number": "+919876543210", "tenant_id": TENANT_B}, headers=TOKEN_A).status_code == 403


def test_wallet_check_fails_closed_for_unknown_tenant():
    ok, _, _ = check_tenant_wallet_balance(f"does_not_exist_{RUN}")
    assert ok is False


def test_simulator_and_sip_controls_protected():
    assert client.post("/api/sim/set-tts?engine=deepgram").status_code == 401
    assert client.post("/api/sim/set-tts?engine=deepgram", headers=TOKEN_A).status_code == 401
    assert client.get("/api/sim/tenants").status_code == 401
    assert client.get("/api/sim/session/sim_deadbeef", headers=TOKEN_A).status_code == 404
    assert client.post("/sip/reconnect").status_code == 401
    assert client.get("/api/sim/tts?text=hi").status_code == 401


def test_chat_history_of_whatsapp_session_not_public():
    assert client.get("/api/v1/chat/history/wa_919999999999").status_code == 404


# --- Reseller economics ---------------------------------------------------------
def test_reseller_topup_debits_reseller_wallet():
    with sync_engine.connect() as conn:
        before = conn.execute(text("SELECT wallet_balance FROM resellers WHERE reseller_id = :r"), {"r": RESELLER}).scalar()
    ok = client.post(f"/api/v1/reseller/clients/{CLIENT_OF_RESELLER}/topup", json={"amount": 40}, headers=TOKEN_RESELLER)
    assert ok.status_code == 200
    with sync_engine.connect() as conn:
        after = conn.execute(text("SELECT wallet_balance FROM resellers WHERE reseller_id = :r"), {"r": RESELLER}).scalar()
    assert round(before - after, 2) == 40.0
    too_much = client.post(f"/api/v1/reseller/clients/{CLIENT_OF_RESELLER}/topup", json={"amount": 10_000}, headers=TOKEN_RESELLER)
    assert too_much.status_code == 402
    assert client.get("/api/v1/reseller/overview?reseller_id=someone_else", headers=TOKEN_RESELLER).status_code == 403


def test_tenant_token_cannot_reach_reseller_or_admin():
    assert client.get("/api/v1/reseller/overview", headers=TOKEN_A).status_code == 401
    assert client.get("/api/v1/admin/overview", headers=TOKEN_A).status_code == 401
    assert client.get("/api/analytics/overview", headers=TOKEN_RESELLER).status_code == 400


# --- Webhooks ---------------------------------------------------------------------
def test_exotel_webhook_token_enforced_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "EXOTEL_WEBHOOK_TOKEN", "hook-secret")
    assert client.post("/exotel/status", data={"CallSid": "x"}).status_code == 403
    assert client.post("/exotel/status?token=hook-secret", data={"CallSid": "x"}).status_code == 200


def test_whatsapp_webhook_requires_signature_for_real_provider(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_PROVIDER", "meta")
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", "app-secret")
    assert client.post("/api/v1/whatsapp/webhook", json={"entry": []}).status_code == 403
    assert client.get("/api/v1/whatsapp/status").status_code == 401


# --- Secrets at rest -------------------------------------------------------------
def test_secret_encryption_roundtrip_and_legacy_plaintext():
    enc = encrypt_secret("exotel-token-123")
    assert enc.startswith("enc:v1:") and "exotel-token-123" not in enc
    assert decrypt_secret(enc) == "exotel-token-123"
    assert decrypt_secret("legacy-plain") == "legacy-plain"
