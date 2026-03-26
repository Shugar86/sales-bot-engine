"""
Retry Policy — Centralized retry/backoff with jitter and circuit breaker.

Provides production-grade retry handling for:
- Network operations (Telegram API, LLM calls)
- Transient failures (429, 503, timeouts)
- Circuit breaker pattern to prevent cascading failures
"""

import asyncio
import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Awaitable, Optional, TypeVar

from ..utils.logger import get_logger

logger = get_logger("retry")

T = TypeVar("T")


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing fast
    HALF_OPEN = "half_open"  # Testing if recovered


@dataclass
class RetryPolicy:
    """Configuration for retry behavior."""
    max_attempts: int = 3
    backoff_base_sec: float = 1.0
    backoff_max_sec: float = 60.0
    jitter: bool = True
    jitter_max_sec: float = 1.0
    retryable_exceptions: tuple = (Exception,)
    on_retry: Optional[Callable[[int, Exception, float], Awaitable[None]]] = None


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    failure_threshold: int = 5           # Failures before opening
    recovery_timeout_sec: float = 60.0   # Time before half-open
    half_open_max_calls: int = 3         # Test calls in half-open state
    success_threshold: int = 2           # Successes to close


class CircuitBreaker:
    """
    Circuit breaker pattern implementation.

    Prevents cascading failures by failing fast when a service
    is consistently failing.
    """

    def __init__(self, name: str, config: CircuitBreakerConfig = None):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._half_open_calls = 0
        self._last_failure_time: Optional[float] = None
        self._lock = asyncio.Lock()

    def _log(self, message: str, **kwargs):
        logger.debug(f"[CircuitBreaker:{self.name}] {message}", extra={
            "circuit_name": self.name,
            "circuit_state": self.state.value,
            **kwargs,
        })

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        """Execute function with circuit breaker protection."""
        async with self._lock:
            if self.state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self.state = CircuitState.HALF_OPEN
                    self._half_open_calls = 0
                    self._log("Transitioning to HALF_OPEN")
                else:
                    raise CircuitBreakerOpenError(
                        f"Circuit {self.name} is OPEN"
                    )

        try:
            result = await fn()
            await self._on_success()
            return result
        except Exception:
            await self._on_failure()
            raise

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to try recovery."""
        if self._last_failure_time is None:
            return True
        elapsed = time.time() - self._last_failure_time
        return elapsed >= self.config.recovery_timeout_sec

    async def _on_success(self):
        """Handle successful call."""
        async with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.config.success_threshold:
                    self.state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
                    self._log("Transitioning to CLOSED")
            else:
                self._failure_count = max(0, self._failure_count - 1)

    async def _on_failure(self):
        """Handle failed call."""
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.OPEN
                self._log("Transitioning to OPEN (half-open failed)")
            elif self._failure_count >= self.config.failure_threshold:
                self.state = CircuitState.OPEN
                self._log("Transitioning to OPEN (threshold reached)")

    def get_status(self) -> dict:
        """Get current circuit status."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "last_failure_at": self._last_failure_time,
        }


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open."""
    pass


async def retry_with_backoff(
    fn: Callable[[], Awaitable[T]],
    policy: RetryPolicy = None,
    name: str = "operation",
) -> T:
    """
    Execute function with retry and exponential backoff.

    Args:
        fn: Async function to execute
        policy: Retry configuration
        name: Operation name for logging

    Returns:
        Result of fn()

    Raises:
        Last exception if all retries exhausted
    """
    policy = policy or RetryPolicy()
    last_exception: Optional[Exception] = None

    for attempt in range(policy.max_attempts):
        try:
            return await fn()
        except policy.retryable_exceptions as e:
            last_exception = e

            if attempt < policy.max_attempts - 1:
                # Calculate backoff
                delay = policy.backoff_base_sec * (2 ** attempt)
                delay = min(delay, policy.backoff_max_sec)

                if policy.jitter:
                    delay += random.uniform(0, policy.jitter_max_sec)

                logger.warning(
                    f"[{name}] Attempt {attempt + 1}/{policy.max_attempts} failed: {e}. "
                    f"Retrying in {delay:.1f}s..."
                )

                # Call on_retry callback if provided
                if policy.on_retry:
                    try:
                        await policy.on_retry(attempt, e, delay)
                    except Exception:
                        pass

                await asyncio.sleep(delay)
            else:
                logger.error(f"[{name}] All {policy.max_attempts} attempts failed: {e}")

    raise last_exception


async def retry_with_circuit_breaker(
    fn: Callable[[], Awaitable[T]],
    circuit_breaker: CircuitBreaker,
    policy: RetryPolicy = None,
    name: str = "operation",
) -> T:
    """
    Execute function with both circuit breaker and retry.

    Args:
        fn: Async function to execute
        circuit_breaker: Circuit breaker instance
        policy: Retry configuration
        name: Operation name for logging

    Returns:
        Result of fn()
    """
    async def _wrapped():
        return await retry_with_backoff(fn, policy, name)

    return await circuit_breaker.call(_wrapped)


# Pre-configured policies for common use cases

TELEGRAM_API_POLICY = RetryPolicy(
    max_attempts=3,
    backoff_base_sec=1.0,
    backoff_max_sec=30.0,
    jitter=True,
    retryable_exceptions=(
        ConnectionError,
        OSError,
        asyncio.TimeoutError,
        Exception,  # Telegram API errors
    ),
)

LLM_API_POLICY = RetryPolicy(
    max_attempts=3,
    backoff_base_sec=2.0,
    backoff_max_sec=60.0,
    jitter=True,
    retryable_exceptions=(
        ConnectionError,
        asyncio.TimeoutError,
    ),
)

TELEGRAM_SEND_POLICY = RetryPolicy(
    max_attempts=3,
    backoff_base_sec=0.5,
    backoff_max_sec=10.0,
    jitter=True,
    retryable_exceptions=(
        ConnectionError,
        OSError,
        asyncio.TimeoutError,
    ),
)


class RetryManager:
    """Manages circuit breakers and provides convenient retry methods."""

    def __init__(self):
        self._circuit_breakers: dict[str, CircuitBreaker] = {}

    def get_circuit_breaker(
        self,
        name: str,
        config: CircuitBreakerConfig = None,
    ) -> CircuitBreaker:
        """Get or create a circuit breaker."""
        if name not in self._circuit_breakers:
            self._circuit_breakers[name] = CircuitBreaker(name, config)
        return self._circuit_breakers[name]

    async def telegram_api_call(
        self,
        fn: Callable[[], Awaitable[T]],
        name: str = "telegram_api",
    ) -> T:
        """Make Telegram API call with retry and circuit breaker."""
        circuit = self.get_circuit_breaker("telegram_api")
        return await retry_with_circuit_breaker(
            fn, circuit, TELEGRAM_API_POLICY, name
        )

    async def llm_api_call(
        self,
        fn: Callable[[], Awaitable[T]],
        name: str = "llm_api",
    ) -> T:
        """Make LLM API call with retry and circuit breaker."""
        circuit = self.get_circuit_breaker("llm_api")
        return await retry_with_circuit_breaker(
            fn, circuit, LLM_API_POLICY, name
        )

    def get_status(self) -> dict:
        """Get status of all circuit breakers."""
        return {
            name: cb.get_status()
            for name, cb in self._circuit_breakers.items()
        }


# Global retry manager instance
_retry_manager: Optional[RetryManager] = None


def get_retry_manager() -> RetryManager:
    """Get the global retry manager."""
    global _retry_manager
    if _retry_manager is None:
        _retry_manager = RetryManager()
    return _retry_manager
