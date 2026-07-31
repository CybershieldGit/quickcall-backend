"""
QwikCall CLI Interview Simulator
Test the 5-question interview loop in your terminal without any audio/phone setup.
"""
import uuid
from app.core.interview_agent import InterviewAgent

def run_cli_test():
    session_id = f"cli_{uuid.uuid4().hex[:6]}"
    agent = InterviewAgent(session_id=session_id)
    
    print("=" * 60)
    print(" 🎙️  QwikCall AI Technical Interview Agent — CLI Demo")
    print("=" * 60)
    
    greeting = agent.get_greeting()
    print(f"\n[Agent]: {greeting}")
    
    while True:
        try:
            user_input = input("\n[Candidate]: ")
            if not user_input.strip():
                continue
                
            result = agent.process_turn(user_input)
            print(f"\n[Agent]: {result['response']}")
            
            if result["is_final"]:
                print("\n" + "=" * 60)
                print(" ✅ Interview Completed Successfully!")
                print("=" * 60)
                break

        except (KeyboardInterrupt, EOFError):
            print("\nExiting simulation.")
            break

if __name__ == "__main__":
    run_cli_test()
