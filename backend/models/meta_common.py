"""Shared plumbing for the real Meta adapters: client, retry policy, usage mapping (PLAN.md §3.11, §7.2)."""
from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx
import openai
from openai import AsyncOpenAI

from backend.budget import BudgetExceeded, Usage
from backend.config import Settings

T = TypeVar("T")

# No retry on 4xx; 429 and 5xx get at most two more attempts. Never an unbounded loop.
RETRY_DELAYS_SEC = (0.5, 1.5)


class MetaAPIError(Exception):
    """A non-2xx answer from an endpoint we call with httpx directly (Voice Transcribe)."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"HTTP {status_code}: {body[:300]}")
        self.status_code = status_code


def make_client(settings: Settings, http_client: httpx.AsyncClient | None = None) -> AsyncOpenAI:
    if settings.meta_api_key is None or not settings.meta_api_key.get_secret_value():
        raise RuntimeError("META_API_KEY is not set; use USE_MOCKS=true or add the key to .env")
    return AsyncOpenAI(base_url=settings.meta_base_url, api_key=settings.meta_api_key.get_secret_value(),
                       max_retries=0, timeout=30.0, http_client=http_client)


def retryable(exc: BaseException) -> bool:
    if isinstance(exc, BudgetExceeded):
        return False
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code == 429 or code >= 500
    return isinstance(exc, (openai.APIConnectionError, httpx.TransportError, asyncio.TimeoutError))


async def with_retries(attempt: Callable[[], Awaitable[T]],
                       sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> T:
    """Run `attempt` (which goes through the budget guard itself, so every try is counted)."""
    for delay in (*RETRY_DELAYS_SEC, None):
        try:
            return await attempt()
        except Exception as exc:
            if delay is None or not retryable(exc):
                raise
            await sleep(delay * random.uniform(0.8, 1.2))
    raise AssertionError("unreachable")


def chat_usage(usage) -> Usage:
    """OpenAI-style chat usage -> budget Usage (reasoning tokens are part of completion_tokens)."""
    if usage is None:
        return Usage()
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) or 0
    return Usage(input_tokens=usage.prompt_tokens or 0, cached_input_tokens=cached,
                 output_tokens=usage.completion_tokens or 0)
