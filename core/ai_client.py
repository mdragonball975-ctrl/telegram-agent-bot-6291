"""OpenAI-compatible chat client (Groq) with retries, backoff and rate limiting."""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx

from core.rate_limiter import RateLimiter

log = logging.getLogger(__name__)


class AIClientError(RuntimeError):
    pass


class AIClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        max_rpm: int = 30,
        timeout: int = 120,
        max_retries: int = 6,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._max_retries = max_retries
        self._limiter = RateLimiter(max_rpm)
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=httpx.Timeout(timeout),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature if temperature is None else temperature,
            "max_tokens": self._max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        last_err: Exception | None = None
        for attempt in range(self._max_retries):
            await self._limiter.acquire()
            try:
                resp = await self._client.post("/chat/completions", json=payload)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_err = exc
                await self._sleep_backoff(attempt)
                continue

            if resp.status_code == 200:
                data = resp.json()
                if not data.get("choices"):
                    raise AIClientError("AI response contained no choices")
                return data["choices"][0]["message"]

            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = self._parse_retry_after(resp)
                last_err = AIClientError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                log.warning("AI call retryable error %s (attempt %d)", resp.status_code, attempt + 1)
                await self._sleep_backoff(attempt, retry_after)
                continue

            raise AIClientError(f"HTTP {resp.status_code}: {resp.text[:500]}")

        raise AIClientError(f"AI call failed after {self._max_retries} retries: {last_err}")

    async def _sleep_backoff(self, attempt: int, retry_after: float | None = None) -> None:
        if retry_after is not None:
            delay = retry_after + random.uniform(0, 0.5)
        else:
            delay = min(60.0, (2 ** attempt)) + random.uniform(0, 1.0)
        await asyncio.sleep(delay)

    @staticmethod
    def _parse_retry_after(resp: httpx.Response) -> float | None:
        header = resp.headers.get("retry-after")
        if header:
            try:
                return float(header)
            except ValueError:
                return None
        return None
