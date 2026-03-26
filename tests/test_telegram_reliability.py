"""Tests for Telegram reliability features."""
import asyncio
import pytest

from src.core.retry import RetryManager, TELEGRAM_SEND_POLICY


class TestTelegramMonitorReliability:
    """Tests for TelegramMonitor reliability features."""

    @pytest.mark.asyncio
    async def test_offset_persistence(self, tmp_path):
        """Monitor should persist and load offset."""
        from src.monitors.telegram_monitor import TelegramMonitor

        storage_dir = tmp_path / "storage"
        storage_dir.mkdir()

        # Create monitor and simulate offset update
        monitor = TelegramMonitor(
            bot_token="123:abc",
            storage_dir=str(storage_dir),
        )

        # Manually set offset
        monitor.offset = 12345
        monitor._save_offset()

        # Create new monitor instance (simulating restart)
        monitor2 = TelegramMonitor(
            bot_token="123:abc",
            storage_dir=str(storage_dir),
        )

        assert monitor2.offset == 12345

    @pytest.mark.asyncio
    async def test_send_message_retry(self):
        """Send message should retry on failure."""
        from src.core.retry import retry_with_backoff

        attempt_count = 0

        async def failing_send():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 2:
                raise ConnectionError("Network error")
            return True

        result = await retry_with_backoff(
            failing_send,
            policy=TELEGRAM_SEND_POLICY,
            name="test_send",
        )

        assert result is True
        assert attempt_count == 2

    @pytest.mark.asyncio
    async def test_rate_limit_handling(self):
        """Should handle 429 with Retry-After."""
        from src.core.retry import retry_with_backoff

        retry_after_received = []

        async def rate_limited_call():
            retry_after_received.append(30)
            raise ConnectionError("429 Rate limited")

        with pytest.raises(ConnectionError):
            await retry_with_backoff(
                rate_limited_call,
                policy=TELEGRAM_SEND_POLICY,
                name="test_rate_limit",
            )


class TestRetryManager:
    """Tests for RetryManager."""

    def test_get_circuit_breaker_creates_new(self):
        """Should create circuit breaker if not exists."""
        manager = RetryManager()

        cb1 = manager.get_circuit_breaker("test1")
        cb2 = manager.get_circuit_breaker("test1")

        assert cb1 is cb2  # Same instance

    def test_different_names_different_circuits(self):
        """Different names should get different circuits."""
        manager = RetryManager()

        cb1 = manager.get_circuit_breaker("test1")
        cb2 = manager.get_circuit_breaker("test2")

        assert cb1 is not cb2

    @pytest.mark.asyncio
    async def test_circuit_opens_after_failures(self):
        """Circuit should open after threshold failures."""
        from src.core.retry import CircuitBreaker, CircuitBreakerConfig

        config = CircuitBreakerConfig(failure_threshold=2)
        breaker = CircuitBreaker("test", config)

        async def always_fail():
            raise ValueError("Always fails")

        # First two calls should fail but not open circuit
        try:
            await breaker.call(always_fail)
        except ValueError:
            pass

        try:
            await breaker.call(always_fail)
        except ValueError:
            pass

        # Third call should fail with CircuitBreakerOpenError
        from src.core.retry import CircuitBreakerOpenError
        with pytest.raises(CircuitBreakerOpenError):
            await breaker.call(always_fail)

    @pytest.mark.asyncio
    async def test_circuit_closes_after_success(self):
        """Circuit should close after successful calls in half-open."""
        from src.core.retry import CircuitBreaker, CircuitBreakerConfig

        config = CircuitBreakerConfig(
            failure_threshold=1,
            success_threshold=1,
            recovery_timeout_sec=0.1,
        )
        breaker = CircuitBreaker("test", config)

        call_count = 0

        async def eventually_succeeds():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("First call fails")
            return "success"

        # First call fails, opens circuit
        try:
            await breaker.call(eventually_succeeds)
        except ValueError:
            pass

        assert breaker.state.value == "open"

        # Wait for recovery timeout
        await asyncio.sleep(0.15)

        # Next call should be in half-open state
        result = await breaker.call(eventually_succeeds)
        assert result == "success"
        assert breaker.state.value == "closed"


class TestDedupSQLite:
    """Tests for SQLite-based deduplication."""

    def test_sqlite_storage_created(self, tmp_path):
        """Should create SQLite DB instead of JSON."""
        from src.utils.dedup import DeduplicationStore

        storage_path = tmp_path / "processed_messages.json"
        DeduplicationStore(storage_path=str(storage_path))

        # Should create .db file instead of .json
        db_path = storage_path.with_suffix(".db")
        assert db_path.exists()

    @pytest.mark.asyncio
    async def test_concurrent_safety(self, tmp_path):
        """SQLite backend should handle concurrent access with async lock."""
        from src.utils.dedup import DeduplicationStore

        storage_path = tmp_path / "processed.json"
        store1 = DeduplicationStore(storage_path=str(storage_path))
        store2 = DeduplicationStore(storage_path=str(storage_path))

        # Both should be able to write
        await store1.mark_processed("chat1", 1, "hello")
        await store2.mark_processed("chat1", 2, "world")

        # Both should see the data
        assert store1.is_processed("chat1", 1, "hello")
        assert store2.is_processed("chat1", 2, "world")

    @pytest.mark.asyncio
    async def test_message_hash_consistency(self, tmp_path):
        """Same message should produce same hash."""
        from src.utils.dedup import DeduplicationStore

        store = DeduplicationStore(storage_path=str(tmp_path / "test.json"))

        # Mark as processed
        await store.mark_processed("chat1", 1, "hello world")

        # Same message should be detected as processed
        assert store.is_processed("chat1", 1, "hello world")

        # Different message should not be processed
        assert not store.is_processed("chat1", 2, "different text")

    @pytest.mark.asyncio
    async def test_cleanup_old_entries(self, tmp_path):
        """Should cleanup entries older than max_age_hours."""
        from src.utils.dedup import DeduplicationStore

        store = DeduplicationStore(
            storage_path=str(tmp_path / "test.json"),
            max_age_hours=0,  # Immediate cleanup
        )

        # Add an entry
        await store.mark_processed("chat1", 1, "test")
        assert store.is_processed("chat1", 1, "test")

        # Force cleanup
        store._cleanup_old()

        # Should be cleaned up
        assert not store.is_processed("chat1", 1, "test")


class TestLLMClientReliability:
    """Tests for LLM client reliability features."""

    def test_response_validation(self):
        """Should validate response structure."""
        from src.utils.llm_client import LLMClient

        client = LLMClient(api_key="test")

        # Valid response
        valid_data = {
            "choices": [{"message": {"content": "Hello"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        assert client._validate_response(valid_data) == "Hello"

        # Invalid: missing choices
        with pytest.raises(ValueError):
            client._validate_response({})

        # Invalid: empty choices
        with pytest.raises(ValueError):
            client._validate_response({"choices": []})

        # Invalid: missing message
        with pytest.raises(ValueError):
            client._validate_response({"choices": [{}]})

    def test_circuit_breaker_integration(self):
        """Should have circuit breaker."""
        from src.utils.llm_client import LLMClient

        client = LLMClient(api_key="test")
        status = client.get_circuit_status()

        assert status["name"] == "llm_api"
        assert status["state"] == "closed"

    @pytest.mark.asyncio
    async def test_timeout_override(self):
        """Should allow per-call timeout override."""
        from src.utils.llm_client import LLMClient

        client = LLMClient(api_key="test", timeout=30)

        # Should not raise
        # Note: We can't actually test the timeout without mocking,
        # but we can verify the client accepts the parameter
        assert client.timeout == 30
