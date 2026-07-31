"""
Trigger a live outbound call from your Twilio number to your personal mobile phone!
Uses the central outbound_call dispatcher.
"""
import sys
from app.routers.outbound_call import trigger_outbound_call

def place_test_call(target_phone_number: str):
    print(f"Initiating outbound call to {target_phone_number}...")
    try:
        call_sid = trigger_outbound_call(target_phone_number)
        print(f"✅ Call successfully initiated! Call SID: {call_sid}")
        print("Your mobile phone will ring in a few seconds. Answer it to speak with QwikCall Agent!")
    except Exception as e:
        print(f"❌ Failed to initiate call: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        target_number = sys.argv[1]
    else:
        target_number = input("Enter your verified mobile phone number (e.g., +919876543210): ")
    
    place_test_call(target_number.strip())
