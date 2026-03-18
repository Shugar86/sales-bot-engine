"""
LLM Client — OpenRouter API с retry, backoff, logging
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from .logger import get_logger

logger = get_logger("llm")


@dataclass
class LLMResponse:
    """Ответ от LLM"""
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0
    success: bool = True
    error: Optional[str] = None


class LLMClient:
    """OpenRouter API клиент с retry/backoff"""
    
    def __init__(
        self,
        api_key: str,
        api_base: str = "https://openrouter.ai/api/v1",
        timeout: int = 30,
        max_retries: int = 3,
        backoff_base: float = 1.0,
    ):
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._client: Optional[httpx.AsyncClient] = None
    
    @property
    def client(self) -> httpx.AsyncClient:
        """Lazy-init HTTP клиент"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client
    
    async def close(self):
        """Закрыть HTTP клиент"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
    
    async def call(
        self,
        model: str,
        prompt: str,
        system: str = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """
        Вызвать LLM с retry/backoff.
        
        Args:
            model: ID модели (например "google/gemini-2.0-flash-001")
            prompt: Пользовательский промпт
            system: Системный промпт (опционально)
            temperature: Температура генерации
            max_tokens: Макс токенов в ответе
        
        Returns:
            LLMResponse с текстом и метаданными
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        
        for attempt in range(self.max_retries + 1):
            try:
                start = time.monotonic()
                
                response = await self.client.post(
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
                
                if response.status_code == 429:
                    # Rate limited — backoff
                    wait = self.backoff_base * (2 ** attempt)
                    logger.warning(f"Rate limited on {model}, waiting {wait:.1f}s (attempt {attempt+1})")
                    await asyncio.sleep(wait)
                    continue
                
                if response.status_code != 200:
                    error_text = response.text[:200]
                    logger.error(f"API error {response.status_code}: {error_text}")
                    
                    if attempt < self.max_retries:
                        wait = self.backoff_base * (2 ** attempt)
                        await asyncio.sleep(wait)
                        continue
                    
                    return LLMResponse(
                        text="",
                        model=model,
                        latency_ms=latency,
                        success=False,
                        error=f"HTTP {response.status_code}: {error_text}",
                    )
                
                data = response.json()
                
                text = data["choices"][0]["message"]["content"]
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
                )
                
            except httpx.TimeoutException:
                logger.warning(f"Timeout on {model} (attempt {attempt+1})")
                if attempt < self.max_retries:
                    await asyncio.sleep(self.backoff_base * (2 ** attempt))
                    continue
                return LLMResponse(text="", model=model, success=False, error="Timeout")
            
            except Exception as e:
                logger.error(f"LLM call failed: {e}")
                if attempt < self.max_retries:
                    await asyncio.sleep(self.backoff_base * (2 ** attempt))
                    continue
                return LLMResponse(text="", model=model, success=False, error=str(e))
        
        return LLMResponse(text="", model=model, success=False, error="Max retries exceeded")


# === Глобальный клиент (singleton) ===
_client_instance: Optional[LLMClient] = None


def get_llm_client(api_key: str = None, **kwargs) -> LLMClient:
    """Получить глобальный LLM клиент"""
    global _client_instance
    if _client_instance is None:
        if not api_key:
            import os
            api_key = os.getenv("OPENROUTER_API_KEY", "")
        _client_instance = LLMClient(api_key=api_key, **kwargs)
    return _client_instance
