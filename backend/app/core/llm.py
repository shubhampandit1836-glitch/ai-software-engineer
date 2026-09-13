import os
import asyncio
from typing import Optional, Sequence
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from pydantic import SecretStr

# WHY: This module is the FIRST thing loaded. The .env keys must enter
# os.environ BEFORE the tier construction below runs at import time.
load_dotenv()

# ---------- Provider tier definitions ----------
# WHY a tier is a ROLE, not a provider: "smart" means the coding brain,
# wherever it physically runs. When a provider fails at runtime, we rotate
# the SLOT - the tier (and every node using it) is untouched.
# Each entry: (provider_name, factory) - factories are cheap; we build
# lazily per call attempt so a missing key never blocks other providers.

GROQ_FAST_MODEL = "openai/gpt-oss-20b"
GROQ_SMART_MODEL = "qwen/qwen3.8-27b"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_FAST_MODEL = "meta-llama/llama-3.1-8b-instruct:free"
OPENROUTER_SMART_MODEL = "qwen/qwen-2.5-coder-32b-instruct:free"

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_FAST_MODEL = "meta/llama-3.1-8b-instruct"
NVIDIA_SMART_MODEL = "qwen/qwen2.5-coder-32b-instruct"


def _groq_key() -> Optional[SecretStr]:
    key = os.environ.get("GROQ_API_KEY")
    return SecretStr(key) if key else None


def _openrouter_key() -> Optional[SecretStr]:
    key = os.environ.get("OPENROUTER_API_KEY")
    return SecretStr(key) if key else None


def _nvidia_key() -> Optional[SecretStr]:
    key = os.environ.get("NVIDIA_API_KEY")
    return SecretStr(key) if key else None


def _build_groq_fast() -> BaseChatModel:
    return ChatGroq(
        model=GROQ_FAST_MODEL,
        api_key=_groq_key(),
        temperature=0.1,
        max_tokens=500,
    )


def _build_groq_smart() -> BaseChatModel:
    return ChatGroq(
        model=GROQ_SMART_MODEL,
        api_key=_groq_key(),
        temperature=0.2,
        max_tokens=950,
    )


def _build_openrouter_fast() -> BaseChatModel:
    return ChatOpenAI(
        model=OPENROUTER_FAST_MODEL,
        api_key=_openrouter_key(),
        base_url=OPENROUTER_BASE_URL,
        temperature=0.1,
        max_tokens=500,
    )


def _build_openrouter_smart() -> BaseChatModel:
    return ChatOpenAI(
        model=OPENROUTER_SMART_MODEL,
        api_key=_openrouter_key(),
        base_url=OPENROUTER_BASE_URL,
        temperature=0.2,
        max_tokens=950,
    )


def _build_nvidia_fast() -> BaseChatModel:
    return ChatOpenAI(
        model=NVIDIA_FAST_MODEL,
        api_key=_nvidia_key(),
        base_url=NVIDIA_BASE_URL,
        temperature=0.1,
        max_tokens=500,
    )


def _build_nvidia_smart() -> BaseChatModel:
    return ChatOpenAI(
        model=NVIDIA_SMART_MODEL,
        api_key=_nvidia_key(),
        base_url=NVIDIA_BASE_URL,
        temperature=0.2,
        max_tokens=950,
    )


# WHY: skip providers whose key is absent - a missing OPENROUTER_API_KEY
# must degrade the chain gracefully (Groq -> NVIDIA), never crash it.
FAST_PROVIDERS = [
    ("groq", _build_groq_fast),
    ("openrouter", _build_openrouter_fast),
    ("nvidia", _build_nvidia_fast),
]

SMART_PROVIDERS = [
    ("groq", _build_groq_smart),
    ("openrouter", _build_openrouter_smart),
    ("nvidia", _build_nvidia_smart),
]

# ---------- Back-compat handles ----------
# WHY: nodes.py and answer_nodes.py import llm_fast / llm_smart directly.
# Keeping these names means v3.5 changes ZERO call sites - the failover
# lives entirely inside invoke_with_retry.
llm_fast = _build_groq_fast()
llm_smart = _build_groq_smart()

MAX_ATTEMPTS_PER_PROVIDER = 3
RATE_LIMIT_WAIT_SECONDS = 20
TRANSIENT_WAIT_SECONDS = 2


def _is_rate_limit(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "rate_limit" in text


def _is_provider_error(exc: Exception) -> bool:
    """True when this error means 'rotate provider', False when it means
    'the request itself is broken - retrying elsewhere cannot help'.

    WHY the distinction: 429/5xx/auth/network = the PROVIDER is having a
    bad time. ContentFilter/prompt-too-long/invalid-request = OUR request
    is bad, and the same request will fail identically on every provider.
    """
    text = str(exc).lower()
    provider_markers = [
        "429", "rate limit", "rate_limit",
        "500", "502", "503", "504", "server error", "overloaded",
        "timeout", "timed out", "connection", "network",
        "unauthorized", "invalid api key", "401", "403",
        "service unavailable", "capacity",
    ]
    return any(marker in text for marker in provider_markers)


async def _try_invoke(llm: BaseChatModel, messages: Sequence[BaseMessage]) -> BaseMessage:
    """Per-provider retry with rate-limit-aware backoff.

    WHY 20s on rate limits: OTPM budgets refill per MINUTE, so waiting
    preserves the strong primary provider. The flat 2s wait rotated to
    the fallback model almost instantly - and the weaker model broke the
    milestone run. The v2 behavior that PASSED the milestone waited 20s;
    this restores that patience inside the cascade.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(1, MAX_ATTEMPTS_PER_PROVIDER + 1):
        try:
            return await llm.ainvoke(messages)
        except Exception as e:
            last_exc = e
            if attempt == MAX_ATTEMPTS_PER_PROVIDER:
                break
            wait = RATE_LIMIT_WAIT_SECONDS if _is_rate_limit(e) else TRANSIENT_WAIT_SECONDS
            await asyncio.sleep(wait)
    assert last_exc is not None  # for the type checker: loop always sets it on failure
    raise last_exc


async def invoke_with_retry(
    messages: Sequence[BaseMessage],
    tier: str = "smart",
) -> BaseMessage:
    """Invokes an LLM with automatic provider failover.

    Signature CHANGED from v2: messages first, tier selector second.
    A tier ('fast'|'smart') maps to a provider cascade; failures rotate
    Groq -> OpenRouter -> NVIDIA without any caller knowing.
    """
    providers = FAST_PROVIDERS if tier == "fast" else SMART_PROVIDERS

    errors: list[str] = []
    for name, factory in providers:
        # WHY factory here, not at module import: a provider with no key
        # constructs an unusable client - building lazily lets us check
        # availability per call instead.
        key_env = {
            "groq": "GROQ_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "nvidia": "NVIDIA_API_KEY",
        }[name]
        if not os.environ.get(key_env):
            errors.append(f"{name}: no api key")
            continue

        try:
            llm = factory()
            return await _try_invoke(llm, messages)
        except Exception as e:
            if _is_provider_error(e):
                errors.append(f"{name}: {str(e)[:120]}")
                continue  # rotate to next provider
            # Request-level error: same fate everywhere - raise now.
            raise

    raise RuntimeError(
        "All providers failed. Attempt log:\n- " + "\n- ".join(errors)
    )