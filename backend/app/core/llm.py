import os
import asyncio
from typing import Sequence
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.messages import BaseMessage
from pydantic import SecretStr

# WHY: This module is the FIRST thing loaded. The .env keys must enter
# os.environ BEFORE _build_llm() runs at import time below.
load_dotenv()

# --- Model Tiers ---
# WHY: Not every node needs a genius model. Classification and guardrails
# need speed, not intelligence. Only the coder needs the big brain.
FAST_MODEL = "openai/gpt-oss-20b"      # Intent, guardrails, synthesizer
SMART_MODEL = "qwen/qwen3.8-27b"       # Planner, Coder, Reviewer

def _get_api_key() -> SecretStr:
    # WHY: Fail FAST with a clear message at the door, instead of a cryptic
    # GroqError deep inside the SDK. Environment problems must be obvious.
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError(
            "GROQ_API_KEY not found. Create backend/.env containing: "
            "GROQ_API_KEY=gsk_your_key_here"
        )
    return SecretStr(key)

def _build_llm(model: str, temperature: float, max_tokens: int) -> ChatGroq:
    return ChatGroq(
        model=model,
        api_key=_get_api_key(),
        temperature=temperature,
        # WHY: THE 429 KILLER. Groq free tier rejects any request whose
        # EXPECTED output exceeds 1000 OTPM. Capping below 1000 always passes.
        max_tokens=max_tokens,
        stop_sequences=[]
    )

llm_fast = _build_llm(FAST_MODEL, temperature=0.1, max_tokens=500)
llm_smart = _build_llm(SMART_MODEL, temperature=0.2, max_tokens=950)

MAX_RETRIES = 3

async def invoke_with_retry(llm: ChatGroq, messages: Sequence[BaseMessage]) -> BaseMessage:
    """
    Invokes an LLM with exponential backoff on rate-limit errors.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return await llm.ainvoke(messages)
        except Exception as e:
            error_str = str(e).lower()
            # WHY: 429s are PER MINUTE. The budget refills over time, so we
            # WAIT (20s, 40s) instead of crashing.
            is_rate_limit = ("429" in error_str or "rate_limit" in error_str)
            is_last_attempt = (attempt == MAX_RETRIES)

            if is_last_attempt:
                raise  # WHY: After 3 tries, surface the real error upstream.

            wait_time = (20 * attempt) if is_rate_limit else 2
            await asyncio.sleep(wait_time)

    raise RuntimeError("LLM invocation failed after all retries")