import os
import json
from typing import Dict, Any, Optional
from groq import Groq
from app.config import settings

# In-memory store for call sessions
# Map call_sid -> session dictionary
SESSIONS: Dict[str, Dict[str, Any]] = {}

class InterviewAgent:
    def __init__(self, session_id: str):
        self.session_id = session_id
        if session_id not in SESSIONS:
            SESSIONS[session_id] = {
                "state": "GREETING",  # GREETING -> TOPIC_SELECTION -> INTERVIEW_LOOP -> COMPLETED
                "topic": None,
                "question_count": 0,
                "max_questions": 5,
                "history": []
            }
        self.session = SESSIONS[session_id]
        
        self.groq_client = None
        if settings.GROQ_API_KEY and settings.GROQ_API_KEY != "your_groq_api_key_here":
            try:
                self.groq_client = Groq(api_key=settings.GROQ_API_KEY)
            except Exception as e:
                print(f"[InterviewAgent] Warning: Failed to init Groq client: {e}")

    def get_greeting(self) -> str:
        return "Hello from QwikCall! Are you ready for your technical interview today?"

    def process_turn(self, user_speech: str) -> Dict[str, Any]:
        """
        Processes candidate voice/text input and returns the agent's spoken response + call status.
        """
        state = self.session["state"]
        user_speech_clean = user_speech.strip()
        
        # Log conversation turn
        self.session["history"].append({"role": "user", "content": user_speech_clean})

        if state == "GREETING":
            self.session["state"] = "TOPIC_SELECTION"
            agent_response = "Awesome! What technical topic would you like to be interviewed on today? For example: Deep Learning, Python, React, or Data Structures."
            self.session["history"].append({"role": "assistant", "content": agent_response})
            return {"response": agent_response, "is_final": False, "question_num": 0}

        elif state == "TOPIC_SELECTION":
            self.session["topic"] = user_speech_clean
            self.session["question_count"] = 1
            self.session["state"] = "INTERVIEW_LOOP"
            
            # Generate Question 1
            agent_response = self._generate_llm_question(
                topic=user_speech_clean,
                question_num=1,
                user_last_answer=None
            )
            self.session["history"].append({"role": "assistant", "content": agent_response})
            return {"response": agent_response, "is_final": False, "question_num": 1}

        elif state == "INTERVIEW_LOOP":
            current_q = self.session["question_count"]
            
            if current_q >= self.session["max_questions"]:
                # Final Question answered, wrap up
                self.session["state"] = "COMPLETED"
                wrapup_msg = f"Thank you for completing the 5-question technical interview on {self.session['topic']}! You provided great insights today. Goodbye from QwikCall!"
                self.session["history"].append({"role": "assistant", "content": wrapup_msg})
                return {"response": wrapup_msg, "is_final": True, "question_num": current_q}
            else:
                next_q = current_q + 1
                self.session["question_count"] = next_q
                
                agent_response = self._generate_llm_question(
                    topic=self.session["topic"],
                    question_num=next_q,
                    user_last_answer=user_speech_clean
                )
                self.session["history"].append({"role": "assistant", "content": agent_response})
                return {"response": agent_response, "is_final": False, "question_num": next_q}

        else:  # COMPLETED
            return {
                "response": "The interview has already ended. Thank you for using QwikCall!",
                "is_final": True,
                "question_num": 5
            }

    def _generate_llm_question(self, topic: str, question_num: int, user_last_answer: Optional[str]) -> str:
        """
        Uses Groq LLM (Llama 3.1 8B Instant) to generate a concise spoken interview question.
        Falls back to intelligent preset questions if Groq key is not configured.
        """
        if not self.groq_client:
            return self._fallback_questions(topic, question_num)

        try:
            system_prompt = (
                f"You are QwikCall, a friendly technical interviewer interviewing a candidate on the topic of '{topic}'. "
                f"You are asking Question {question_num} out of 5. "
                "IMPORTANT INSTRUCTIONS:\n"
                "1. Keep your response extremely brief (1 to 2 short conversational sentences maximum) because this will be spoken aloud over a phone call.\n"
                "2. If the candidate answered a previous question, briefly acknowledge their answer in 5 words or fewer, then ask the next technical question on the topic.\n"
                "3. Do not include markdown, bullet points, code snippets, or special symbols. Write pure spoken text."
            )

            messages = [{"role": "system", "content": system_prompt}]
            
            # Append history for context
            for entry in self.session["history"][-4:]:
                messages.append(entry)

            response = self.groq_client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=messages,
                max_tokens=60,
                temperature=0.5,
            )
            
            reply = response.choices[0].message.content.strip()
            return reply

        except Exception as e:
            print(f"[InterviewAgent] Groq API error: {e}. Using fallback generator.")
            return self._fallback_questions(topic, question_num)

    def _fallback_questions(self, topic: str, question_num: int) -> str:
        """Fallback question generator when LLM API key is not present."""
        presets = {
            1: f"Great! Let's start with question 1 on {topic}. Can you briefly explain the core concept of {topic} in your own words?",
            2: f"Good explanation. For question 2, what is one major advantage and one limitation of using {topic} in production?",
            3: f"Got it! Moving to question 3, how would you debug a performance issue or unexpected error in a {topic} project?",
            4: f"Very clear! Question 4: Can you compare {topic} with a related framework or alternative approach?",
            5: f"Final question! Question 5: What is a real-world project or use case where you applied {topic} successfully?"
        }
        return presets.get(question_num, f"Question {question_num}: Can you tell me more about your experience with {topic}?")
