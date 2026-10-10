#!/usr/bin/env python3
"""
Trigger Outbound AI Voice Call via Exotel
Usage:
    python3 make_call.py [phone_number]

Example:
    python3 make_call.py 9876543210
    python3 make_call.py +919876543210
"""
import sys
import os
import json
import httpx
from dotenv import load_dotenv

# Load env variables
script_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(script_dir, '.env'), override=True)

def get_live_base_url() -> str:
    try:
        import urllib.request
        req = urllib.request.urlopen("http://127.0.0.1:20241/quicktunnel", timeout=1)
        data = json.loads(req.read().decode())
        hostname = data.get("hostname")
        if hostname:
            return f"https://{hostname}"
    except Exception:
        pass

    env_path = os.path.join(script_dir, '.env')
    if os.path.exists(env_path):
        try:
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('BASE_URL=') and not line.startswith('#'):
                        val = line.split('=', 1)[1].strip().strip('"').strip("'")
                        if val:
                            return val.rstrip('/')
        except Exception:
            pass
    return (os.getenv("BASE_URL") or "http://localhost:8001").rstrip("/")

account_sid = os.getenv("EXOTEL_ACCOUNT_SID")
api_key = os.getenv("EXOTEL_API_KEY")
api_token = os.getenv("EXOTEL_API_TOKEN")
subdomain = os.getenv("EXOTEL_SUBDOMAIN", "api.exotel.com")
caller_id = os.getenv("EXOTEL_CALLER_ID")
app_id = os.getenv("EXOTEL_APP_ID")
base_url = get_live_base_url()

if not account_sid or not api_key or not api_token:
    print("❌ Error: EXOTEL credentials (EXOTEL_ACCOUNT_SID, EXOTEL_API_KEY, EXOTEL_API_TOKEN) missing in .env")
    sys.exit(1)

if not caller_id:
    print("❌ Error: EXOTEL_CALLER_ID (provisioned Indian DID) missing in .env")
    sys.exit(1)

# Parse arguments
target_args = [a for a in sys.argv[1:] if not a.startswith("--")]
to_num = target_args[0] if target_args else None

if not to_num:
    print("Usage: python3 make_call.py <phone_number>")
    sys.exit(1)

# Normalize phone number
clean_num = to_num.strip().replace(" ", "").replace("-", "")
if not clean_num.startswith("+"):
    if len(clean_num) == 10:
        clean_num = "+91" + clean_num
    else:
        clean_num = "+" + clean_num

print(f"📞 Initiating Exotel Outbound Voicebot Call:")
print(f"   From (Exotel DID): {caller_id}")
print(f"   To (Customer):     {clean_num}")
print(f"   Webhook Base:      {base_url}")

url = f"https://{api_key}:{api_token}@{subdomain}/v1/Accounts/{account_sid}/Calls/connect.json"
payload = {
    "From": clean_num,
    "CallerId": caller_id,
    "Url": f"{base_url}/exotel/passthru",
    "StatusCallback": f"{base_url}/exotel/status",
    "CustomField": "outbound_voicebot"
}

if app_id:
    payload["AppId"] = app_id

try:
    with httpx.Client(timeout=10.0) as client:
        res = client.post(url, data=payload)
        if res.status_code in (200, 201):
            res_data = res.json()
            call = res_data.get("Call", {})
            print("✅ Call successfully initiated via Exotel!")
            print(f"   Call SID: {call.get('Sid')}")
            print(f"   Status:   {call.get('Status')}")
        else:
            print(f"❌ Exotel API Error ({res.status_code}): {res.text}")
            sys.exit(1)
except Exception as e:
    print(f"❌ Failed to dispatch call: {e}")
    sys.exit(1)
