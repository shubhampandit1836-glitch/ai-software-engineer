import os
import asyncio
import logging
import time
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

logger = logging.getLogger(__name__)

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
        # WHY: SDK default request timeout is 600s - a hung connection
        # during a network blip stalls a run for 10+ minutes (observed:
        # elapsed=1344s during a DNS outage). 30s is ~10x observed
        # fast-tier response time.
        timeout=30,
    )


def _build_groq_smart() -> BaseChatModel:
    return ChatGroq(
        model=GROQ_SMART_MODEL,
        api_key=_groq_key(),
        temperature=0.2,
        max_tokens=950,
        # WHY: smart calls (coder/reviewer) legitimately take longer than
        # fast ones - 90s headroom, still 6x below the SDK default.
        timeout=90,
    )


def _build_openrouter_fast() -> BaseChatModel:
    return ChatOpenAI(
        model=OPENROUTER_FAST_MODEL,
        api_key=_openrouter_key(),
        base_url=OPENROUTER_BASE_URL,
        temperature=0.1,
        max_tokens=500,
        timeout=30,
    )


def _build_openrouter_smart() -> BaseChatModel:
    return ChatOpenAI(
        model=OPENROUTER_SMART_MODEL,
        api_key=_openrouter_key(),
        base_url=OPENROUTER_BASE_URL,
        temperature=0.2,
        max_tokens=950,
        timeout=90,
    )


def _build_nvidia_fast() -> BaseChatModel:
    return ChatOpenAI(
        model=NVIDIA_FAST_MODEL,
        api_key=_nvidia_key(),
        base_url=NVIDIA_BASE_URL,
        temperature=0.1,
        max_tokens=500,
        timeout=30,
    )


def _build_nvidia_smart() -> BaseChatModel:
    return ChatOpenAI(
        model=NVIDIA_SMART_MODEL,
        api_key=_nvidia_key(),
        base_url=NVIDIA_BASE_URL,
        temperature=0.2,
        max_tokens=950,
        timeout=90,
    )


# WHY: skip providers whose key is absent - a missing OPENROUTER_API_KEY
# must degrade the chain gracefully (Groq -> NVIDIA), never crash it.
# WHY nvidia BEFORE openrouter in the fast tier: free OpenRouter queues
# routinely add 10-30s; NVIDIA NIM's free tier answers fast. The fast tier
# is the greeting/classifier path - latency is its whole job. The smart
# tier keeps the milestone-proven order.
FAST_PROVIDERS = [
    ("groq", _build_groq_fast),
    ("nvidia", _build_nvidia_fast),
    ("openrouter", _build_openrouter_fast),
]

SMART_PROVIDERS = [
    ("groq", _build_groq_smart),
    ("openrouter", _build_openrouter_smart),
    ("nvidia", _build_nvidia_smart),
]

# ---------- Back-compat handles ----------
# WHY: answer_nodes.py imports llm_fast / llm_smart directly. Keeping these
# names means v3.5 changes ZERO call sites - the failover lives entirely
# inside invoke_with_retry.
llm_fast = _build_groq_fast()
llm_smart = _build_groq_smart()

MAX_ATTEMPTS_PER_PROVIDER = 3
RATE_LIMIT_WAIT_SECONDS = 20
TRANSIENT_WAIT_SECONDS = 2
# WHY one held retry per provider on 429: waiting twice (2 x 20s) keeps a
# run stuck 40s+ on a provider whose per-minute quota is already dead;
# after one held retry fails, rotating is strictly better.
MAX_RATE_LIMIT_RETRIES_PER_PROVIDER = 1

# WHY module-level: maps provider name -> its env var, both for the
# availability check and as documentation of every supported provider.
_KEY_ENV = {
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
}


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


async def _try_invoke(
    llm: BaseChatModel,
    messages: Sequence[BaseMessage],
    rate_limit_wait: float = RATE_LIMIT_WAIT_SECONDS,
) -> BaseMessage:
    """Per-provider retry with rate-limit-aware backoff.

    WHY the rate_limit_wait parameter: OTPM budgets refill per MINUTE, so
    the SMART tier waits out the window to preserve the strong primary
    provider (quality over latency). The FAST tier passes 0.0 - a greeting
    or classification must cascade to the next provider IMMEDIATELY
    instead of sitting out a window (the 15-20s 'hi' bug). Each provider
    has its own quota, so the next provider is very likely free.
    """
    last_exc: Optional[Exception] = None
    rate_limit_retries = 0
    for attempt in range(1, MAX_ATTEMPTS_PER_PROVIDER + 1):
        try:
            return await llm.ainvoke(messages)
        except Exception as e:
            last_exc = e
            if _is_rate_limit(e):
                if rate_limit_wait <= 0.0:
                    # WHY: cascading now beats burning two more instant
                    # 429s against a provider whose quota is exhausted.
                    break
                if rate_limit_retries >= MAX_RATE_LIMIT_RETRIES_PER_PROVIDER:
                    break
                rate_limit_retries += 1
                await asyncio.sleep(rate_limit_wait)
                continue
            if attempt == MAX_ATTEMPTS_PER_PROVIDER:
                break
            await asyncio.sleep(TRANSIENT_WAIT_SECONDS)
    assert last_exc is not None  # for the type checker: loop always sets it on failure
    raise last_exc


async def invoke_with_retry(
    messages: Sequence[BaseMessage],
    tier: str = "smart",
) -> BaseMessage:
    """Invokes an LLM with automatic provider failover.

    Signature CHANGED from v2: messages first, tier selector second.
    A tier ('fast'|'smart') maps to a provider cascade; failures rotate
    providers without any caller knowing. Backoff is tier-aware:
    smart HOLDS the strong provider through a 429 window; fast cascades
    immediately.
    """
    providers = FAST_PROVIDERS if tier == "fast" else SMART_PROVIDERS
    rate_limit_wait = 0.0 if tier == "fast" else RATE_LIMIT_WAIT_SECONDS
    started = time.perf_counter()

    errors: list[str] = []
    for name, factory in providers:
        # WHY factory here, not at module import: a provider with no key
        # constructs an unusable client - building lazily lets us check
        # availability per call instead.
        if not os.environ.get(_KEY_ENV[name]):
            errors.append(f"{name}: no api key")
            continue

        try:
            llm = factory()
            result = await _try_invoke(llm, messages, rate_limit_wait=rate_limit_wait)
            # WHY one line per LLM call: every future "why is this slow?"
            # becomes answerable from the terminal (provider + elapsed).
            logger.info(
                "llm ok: tier=%s provider=%s elapsed=%.1fs chars=%d",
                tier,
                name,
                time.perf_counter() - started,
                len(result.content or ""),
            )
            return result
        except Exception as e:
            if _is_provider_error(e):
                errors.append(f"{name}: {str(e)[:120]}")
                logger.warning(
                    "llm provider error: tier=%s provider=%s (%s)",
                    tier,
                    name,
                    str(e)[:200],
                )
                continue  # rotate to next provider
            # Request-level error: same fate everywhere - raise now.
            raise

    raise RuntimeError(
        "All providers failed. Attempt log:\n- " + "\n- ".join(errors)
    )