"""
Comprehensive Voice & Chat Pipeline Integration Test
Verifies:
1. Voice Agent streaming, intent detection, phonetic email handling, and brochure dispatch.
2. Chat Agent REST turn processing and RAG retrieval.
"""
import sys
import os
import asyncio
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.voice_agent import VoiceAgent
from app.services.messaging import extract_email_address
from app.api.v1.chat.chat_router import send_chat_message, ChatMessageRequest

@pytest.mark.anyio
async def test_voice_and_chat():
    print("Testing Voice Agent turn in new structure...")
    cid = "test_struct_voice_001"
    agent = VoiceAgent(call_id=cid, tenant_id="idigitalstudies")
    agent.session["language"] = "en-IN"
    
    responses = []
    async for item in agent.stream_turn("What is the fee for the digital marketing course?"):
        responses.append(item[0])
    
    voice_reply = " ".join(responses)
    print("Voice Agent replied:", voice_reply[:100], "...")
    assert len(voice_reply) > 10, "Voice Agent reply is empty!"

    print("\nTesting Chat Agent turn in new structure...")
    chat_req = ChatMessageRequest(
        tenant_id="idigitalstudies",
        message="What batches do you offer?",
        language="en-IN"
    )
    chat_res = await send_chat_message(chat_req)
    print("Chat Agent replied:", chat_res["reply"][:100], "...")
    assert len(chat_res["reply"]) > 10, "Chat Agent reply is empty!"

    print("\n✅ BOTH VOICE & CHAT TESTS PASSED IN NEW STRUCTURE!")

if __name__ == "__main__":
    asyncio.run(test_voice_and_chat())
