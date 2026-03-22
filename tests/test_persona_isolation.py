"""Tests for persona runtime isolation."""
import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.orchestrator import SalesBotOrchestrator, PersonaRuntime
from src.core.persona_manager import PersonaConfig, AntiSpamConfig, GroupModeConfig


class TestPersonaIsolation:
    """Tests that personas are properly isolated."""

    def test_runtimes_have_independent_memory(self, tmp_path):
        """Each persona should have its own memory directory."""
        orchestrator = SalesBotOrchestrator(
            personas_dir=str(tmp_path / "personas"),
            memory_dir=str(tmp_path / "memory"),
            openrouter_api_key="test",
        )

        # Create mock configs
        config1 = PersonaConfig(
            name="Persona One",
            anti_spam=AntiSpamConfig(),
            group_mode=GroupModeConfig(max_messages_per_hour=10),
        )
        config2 = PersonaConfig(
            name="Persona Two",
            anti_spam=AntiSpamConfig(),
            group_mode=GroupModeConfig(max_messages_per_hour=10),
        )

        # Build runtimes
        runtime1 = orchestrator._build_runtime(config1)
        runtime2 = orchestrator._build_runtime(config2)

        # Memory stores should be different instances
        assert runtime1.memory is not runtime2.memory

        # Memory directories should be different
        assert "persona_one" in runtime1.memory.memory_dir.lower()
        assert "persona_two" in runtime2.memory.memory_dir.lower()

    def test_runtimes_have_independent_dedup(self, tmp_path):
        """Each persona should have its own dedup store."""
        orchestrator = SalesBotOrchestrator(
            personas_dir=str(tmp_path / "personas"),
            memory_dir=str(tmp_path / "memory"),
            openrouter_api_key="test",
        )

        config1 = PersonaConfig(
            name="Persona One",
            anti_spam=AntiSpamConfig(),
            group_mode=GroupModeConfig(max_messages_per_hour=10),
        )
        config2 = PersonaConfig(
            name="Persona Two",
            anti_spam=AntiSpamConfig(),
            group_mode=GroupModeConfig(max_messages_per_hour=10),
        )

        runtime1 = orchestrator._build_runtime(config1)
        runtime2 = orchestrator._build_runtime(config2)

        # Dedup stores should be different instances
        assert runtime1.dedup is not runtime2.dedup

    def test_runtimes_have_independent_antispam(self, tmp_path):
        """Each persona should have its own rate limiter."""
        orchestrator = SalesBotOrchestrator(
            personas_dir=str(tmp_path / "personas"),
            memory_dir=str(tmp_path / "memory"),
            openrouter_api_key="test",
        )

        config1 = PersonaConfig(
            name="Persona One",
            anti_spam=AntiSpamConfig(min_delay_between_messages=10),
            group_mode=GroupModeConfig(max_messages_per_hour=5),
        )
        config2 = PersonaConfig(
            name="Persona Two",
            anti_spam=AntiSpamConfig(min_delay_between_messages=20),
            group_mode=GroupModeConfig(max_messages_per_hour=10),
        )

        runtime1 = orchestrator._build_runtime(config1)
        runtime2 = orchestrator._build_runtime(config2)

        # Anti-spam should be different instances
        assert runtime1.antispam is not runtime2.antispam

        # Anti-spam should have different configurations
        assert runtime1.antispam.min_delay_sec == 10
        assert runtime2.antispam.min_delay_sec == 20

    def test_runtimes_have_independent_llm_clients(self, tmp_path):
        """Each persona should have its own LLM client."""
        orchestrator = SalesBotOrchestrator(
            personas_dir=str(tmp_path / "personas"),
            memory_dir=str(tmp_path / "memory"),
            openrouter_api_key="test",
        )

        config1 = PersonaConfig(
            name="Persona One",
            anti_spam=AntiSpamConfig(),
            group_mode=GroupModeConfig(max_messages_per_hour=10),
        )
        config2 = PersonaConfig(
            name="Persona Two",
            anti_spam=AntiSpamConfig(),
            group_mode=GroupModeConfig(max_messages_per_hour=10),
        )

        runtime1 = orchestrator._build_runtime(config1)
        runtime2 = orchestrator._build_runtime(config2)

        # LLM clients should be different instances
        assert runtime1.llm is not runtime2.llm


class TestPersonaCrashIsolation:
    """Tests that one persona crash doesn't affect others."""

    @pytest.mark.asyncio
    async def test_one_crash_doesnt_stop_others(self, tmp_path):
        """One persona crashing should not stop other personas."""
        from src.core.lifecycle import LifecycleManager, PersonaSupervisor, SupervisorConfig

        results = {}

        async def make_task(name, should_crash):
            async def task():
                await asyncio.sleep(0.01)
                if should_crash:
                    raise ValueError(f"{name} crashed!")
                results[name] = "completed"
            return task

        manager = LifecycleManager()

        # Register three personas, middle one crashes
        for i, (name, crash) in enumerate([
            ("ok1", False),
            ("crash", True),
            ("ok2", False),
        ]):
            task_factory = await make_task(name, crash)
            supervisor = PersonaSupervisor(
                name,
                task_factory,
                SupervisorConfig(max_restarts=0, backoff_base_sec=0.01),
            )
            manager.register(name, supervisor)

        # Start all
        await manager.start_all()

        # Wait for tasks to complete
        await asyncio.sleep(0.1)

        # ok1 and ok2 should have completed
        assert results.get("ok1") == "completed"
        assert results.get("ok2") == "completed"

        # crash should have recorded errors
        assert manager.supervisors["crash"].health.total_errors >= 1

        await manager.stop_all()


class TestPersonaMemoryIsolation:
    """Tests for memory isolation between personas."""

    def test_user_memory_not_shared(self, tmp_path):
        """User data in one persona should not be visible in another."""
        from src.memory.user_memory import UserMemoryStore

        # Two different memory directories
        memory1 = UserMemoryStore(
            memory_dir=str(tmp_path / "memory1"),
            persona_name="Persona1",
        )
        memory2 = UserMemoryStore(
            memory_dir=str(tmp_path / "memory2"),
            persona_name="Persona2",
        )

        # Record in memory1
        memory1.record_dm(
            user_id="user1",
            username="alice",
            display_name="Alice",
            message="Hello",
            response="Hi",
            stage="engage",
        )

        # Should be visible in memory1
        ctx1 = memory1.get_user_context("user1")
        assert "Alice" in ctx1

        # Should NOT be visible in memory2
        ctx2 = memory2.get_user_context("user1")
        assert "Alice" not in ctx2

        memory1.close()
        memory2.close()


class TestPersonaResourceCleanup:
    """Tests for proper resource cleanup on shutdown."""

    @pytest.mark.asyncio
    async def test_orchestrator_cleanup_on_stop(self, tmp_path):
        """Orchestrator should clean up resources on stop."""
        orchestrator = SalesBotOrchestrator(
            personas_dir=str(tmp_path / "personas"),
            memory_dir=str(tmp_path / "memory"),
            openrouter_api_key="test",
        )

        # Create a mock runtime
        runtime = MagicMock()
        runtime.config.name = "test"
        runtime.monitor = MagicMock()
        runtime.monitor.stop = AsyncMock()
        runtime.llm.close = AsyncMock()
        runtime.memory.close = MagicMock()

        orchestrator.runtimes["test"] = runtime

        # Stop orchestrator
        await orchestrator.stop()

        # Cleanup should have been called
        runtime.monitor.stop.assert_called_once()
        runtime.llm.close.assert_called_once()
        runtime.memory.close.assert_called_once()


class TestPersonaStatus:
    """Tests for persona status reporting."""

    def test_get_status_includes_all_personas(self, tmp_path):
        """Status should include all personas."""
        orchestrator = SalesBotOrchestrator(
            personas_dir=str(tmp_path / "personas"),
            memory_dir=str(tmp_path / "memory"),
            openrouter_api_key="test",
        )

        # Create mock runtimes
        for name in ["p1", "p2", "p3"]:
            config = PersonaConfig(
                name=name,
                platform="telegram",
                account_type="userbot",
                anti_spam=AntiSpamConfig(),
                group_mode=GroupModeConfig(max_messages_per_hour=10),
            )
            runtime = orchestrator._build_runtime(config)
            orchestrator.runtimes[name] = runtime

        status = orchestrator.get_status()

        assert "personas" in status
        assert "p1" in status["personas"]
        assert "p2" in status["personas"]
        assert "p3" in status["personas"]

        # Each persona should have expected fields
        for name, p_status in status["personas"].items():
            assert "state" in p_status
            assert "platform" in p_status
            assert "stats" in p_status
