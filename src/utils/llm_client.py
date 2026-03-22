"""
LLM Client — OpenRouter API с retry, backoff, circuit breaker, logging
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

import httpx

from .logger import get_logger
from ..core.retry import CircuitBreaker, CircuitBreakerConfig, LLM_API_POLICY, retry_with_backoff

logger = get_logger("llm")


@dataclass
class LLMResponse:
    """Response from LLM"""
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0
    success: bool = True
    error: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = field(default=None, repr=False)


class LLMClient:
    """OpenRouter API client with retry, backoff, circuit breaker"""

    def __init__(
        self,
        api_key: str,
        api_base: str = "https://openrouter.ai/api/v1",
        timeout: int = 30,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._client: Optional[httpx.AsyncClient] = None

        # Circuit breaker for the LLM API
        if circuit_breaker is None:
            circuit_config = CircuitBreakerConfig(
                failure_threshold=5,
                recovery_timeout_sec=60.0,
                half_open_max_calls=2,
                success_threshold=2,
            )
            self._circuit = CircuitBreaker("llm_api", circuit_config)
        else:
            self._circuit = circuit_breaker

    @property
    def client(self) -> httpx.AsyncClient:
        """Lazy-init HTTP client"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self):
        """Close HTTP client"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _validate_response(self, data: Dict[str, Any]) -> str:
        """
        Validate and extract text from LLM response.

        Args:
            data: Response JSON from API

        Returns:
            Extracted text content

        Raises:
            ValueError: If response structure is invalid
        """
        if not isinstance(data, dict):
            raise ValueError(f"Response is not a dict: {type(data)}")

        choices = data.get("choices")
        if not choices or not isinstance(choices, list):
            raise ValueError(f"Missing or invalid 'choices' in response: {data.keys()}")

        if len(choices) == 0:
            raise ValueError("Empty 'choices' array in response")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise ValueError(f"Invalid choice type: {type(first_choice)}")

        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise ValueError(f"Missing or invalid 'message' in choice: {first_choice.keys()}")

        content = message.get("content")
        if content is None:
            raise ValueError(f"Missing 'content' in message: {message.keys()}")

        return str(content)
    
    async def call(
        self,
        model: str,
        prompt: str,
        system: str = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        timeout: int = None,
    ) -> LLMResponse:
        """
        Call LLM with retry/backoff/circuit breaker.

        Args:
            model: Model ID (e.g., "google/gemini-2.0-flash-001")
            prompt: User prompt
            system: System prompt (optional)
            temperature: Generation temperature
            max_tokens: Max tokens in response
            timeout: Override timeout for this call

        Returns:
            LLMResponse with text and metadata
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        # Per-call timeout override (router may need shorter timeout)
        call_timeout = timeout or self.timeout

        async def _do_call():
            start = time.monotonic()

            # Use a temporary client with per-call timeout
            client = httpx.AsyncClient(timeout=call_timeout)
            try:
                response = await client.post(
                    f"{self.api_base}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://github.com/sales-bot-engine",
                    },
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    },
                )

                latency = (time.monotonic() - start) * 1000

                # Handle 429 with Retry-After header
                if response.status_code == 429:
                    retry_after = response.headers.get("retry-after")
                    if retry_after:
                        wait = float(retry_after)
                    else:
                        # Fallback to response body
                        data = response.json() if response.text else {}
                        wait = data.get("error", {}).get("metadata", {}).get("retry_after", 30)
                    logger.warning(f"Rate limited on {model}, waiting {wait:.1f}s")
                    await asyncio.sleep(wait)
                    raise ConnectionError(f"429 Rate limited (retry_after: {wait})")

                response.raise_for_status()

                data = response.json()

                # Validate and extract text
                text = self._validate_response(data)
                usage = data.get("usage", {})

                logger.info(
                    f"LLM call: {model} | "
                    f"in={usage.get('prompt_tokens', '?')} out={usage.get('completion_tokens', '?')} | "
                    f"{latency:.0f}ms"
                )

                return LLMResponse(
                    text=text,
                    model=model,
                    tokens_in=usage.get("prompt_tokens", 0),
                    tokens_out=usage.get("completion_tokens", 0),
                    latency_ms=latency,
                    success=True,
                    raw_response=data,
                )
            finally:
                await client.aclose()

        try:
            # Use circuit breaker + retry
            result = await self._circuit.call(
                lambda: retry_with_backoff(
                    _do_call,
                    policy=LLM_API_POLICY,
                    name=f"llm_call:{model}",
                )
            )
            return result
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return LLMResponse(text="", model=model, success=False, error=str(e))

    def get_circuit_status(self) -> Dict[str, Any]:
        """Get circuit breaker status."""
        return self._circuit.get_status()


# === Global client (singleton) ===
_client_instance: Optional[LLMClient] = None


def get_llm_client(api_key: str = None, **kwargs) -> LLMClient:
    """Get global LLM client (singleton)"""
    global _client_instance
    if _client_instance is None:
        if not api_key:
            import os
            api_key = os.getenv("OPENROUTER_API_KEY", "")
        _client_instance = LLMClient(api_key=api_key, **kwargs)
    return _client_instance


def reset_llm_client():
    """Reset the global client (useful for cleanup)."""
    global _client_instance
    _client_instance = None
