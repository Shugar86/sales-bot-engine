"""Tests for lifecycle management and task supervision."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.core.lifecycle import (
    PersonaSupervisor,
    LifecycleManager,
    SupervisorConfig,
    TaskState,
)


class TestPersonaSupervisor:
    """Tests for PersonaSupervisor."""

    @pytest.mark.asyncio
    async def test_supervisor_starts_task(self):
        """Supervisor should start the task factory."""
        task_started = False

        async def task_factory():
            nonlocal task_started
            task_started = True
            await asyncio.sleep(0.1)

        supervisor = PersonaSupervisor("test", task_factory)
        await supervisor.start()

        # Wait a bit for task to start
        await asyncio.sleep(0.05)

        assert supervisor.is_running()
        assert task_started

        await supervisor.stop()

    @pytest.mark.asyncio
    async def test_supervisor_restart_on_crash(self):
        """Supervisor should restart crashed tasks."""
        start_count = 0

        async def crashing_task():
            nonlocal start_count
            start_count += 1
            if start_count < 2:
                raise ValueError("Crash!")
            await asyncio.sleep(0.1)

        config = SupervisorConfig(max_restarts=3, backoff_base_sec=0.01)
        supervisor = PersonaSupervisor("test", crashing_task, config)

        await supervisor.start()
        await asyncio.sleep(0.1)

        assert supervisor.health.restart_count >= 1
        assert start_count >= 2

        await supervisor.stop()

    @pytest.mark.asyncio
    async def test_supervisor_exhaustion(self):
        """Supervisor should give up after max restarts."""
        start_count = 0

        async def always_crash():
            nonlocal start_count
            start_count += 1
            raise ValueError("Always crashes")

        config = SupervisorConfig(max_restarts=2, backoff_base_sec=0.01)
        supervisor = PersonaSupervisor("test", always_crash, config)

        await supervisor._supervise()  # Run directly to completion

        assert supervisor.health.state == TaskState.EXHAUSTED
        assert supervisor.health.restart_count == 2
        assert start_count == 3  # Initial + 2 restarts

    @pytest.mark.asyncio
    async def test_supervisor_graceful_stop(self):
        """Stop should cancel task gracefully."""
        async def long_task():
            await asyncio.sleep(100)

        supervisor = PersonaSupervisor("test", long_task)
        await supervisor.start()

        await asyncio.sleep(0.05)
        assert supervisor.is_running()

        await supervisor.stop(timeout=1.0)
        assert not supervisor.is_running()
        assert supervisor.health.state == TaskState.STOPPED

    @pytest.mark.asyncio
    async def test_supervisor_cancellation_propagation(self):
        """CancelledError should propagate without restart."""
        async def cancelled_task():
            raise asyncio.CancelledError()

        supervisor = PersonaSupervisor("test", cancelled_task)

        with pytest.raises(asyncio.CancelledError):
            await supervisor._supervise()

        assert supervisor.health.state == TaskState.STOPPED
        assert supervisor.health.restart_count == 0

    @pytest.mark.asyncio
    async def test_health_tracking(self):
        """Health metrics should be tracked."""
        async def healthy_task():
            await asyncio.sleep(0.1)

        supervisor = PersonaSupervisor("test", healthy_task)
        await supervisor.start()
        await asyncio.sleep(0.15)

        health = supervisor.get_health()
        assert health["persona_name"] == "test"
        assert health["is_running"] or supervisor.health.state == TaskState.STOPPED
        assert "uptime_sec" in health

        await supervisor.stop()


class TestLifecycleManager:
    """Tests for LifecycleManager."""

    @pytest.mark.asyncio
    async def test_register_and_start_all(self):
        """Manager should start all registered supervisors."""
        started = []

        async def make_task(name):
            async def task():
                started.append(name)
                await asyncio.sleep(0.1)
            return task

        manager = LifecycleManager()

        for name in ["p1", "p2", "p3"]:
            supervisor = PersonaSupervisor(name, await make_task(name))
            manager.register(name, supervisor)

        await manager.start_all()
        await asyncio.sleep(0.05)

        assert len(started) == 3
        await manager.stop_all()

    @pytest.mark.asyncio
    async def test_stop_all_gracefully(self):
        """Manager should stop all supervisors gracefully."""
        async def long_task():
            await asyncio.sleep(100)

        manager = LifecycleManager()

        for name in ["p1", "p2"]:
            supervisor = PersonaSupervisor(name, long_task)
            manager.register(name, supervisor)
            await supervisor.start()

        await asyncio.sleep(0.05)

        await manager.stop_all(timeout=1.0)

        for name, sup in manager.supervisors.items():
            assert not sup.is_running()

    @pytest.mark.asyncio
    async def test_get_health_aggregates(self):
        """Manager should aggregate health from all supervisors."""
        async def task():
            await asyncio.sleep(0.1)

        manager = LifecycleManager()

        for name in ["p1", "p2"]:
            supervisor = PersonaSupervisor(name, task)
            manager.register(name, supervisor)

        await manager.start_all()
        await asyncio.sleep(0.05)

        health = manager.get_health()
        assert "p1" in health
        assert "p2" in health

        await manager.stop_all()

    @pytest.mark.asyncio
    async def test_isolated_persona_failures(self):
        """One persona crash should not affect others."""
        results = {}

        async def crashing_task(name):
            async def task():
                await asyncio.sleep(0.01)
                if name == "crash":
                    raise ValueError("Crash!")
                results[name] = "completed"
            return task

        manager = LifecycleManager()

        for name in ["ok1", "crash", "ok2"]:
            task_factory = await crashing_task(name)
            supervisor = PersonaSupervisor(
                name,
                task_factory,
                SupervisorConfig(max_restarts=0, backoff_base_sec=0.01),
            )
            manager.register(name, supervisor)

        await manager.start_all()
        await asyncio.sleep(0.1)

        # ok1 and ok2 should complete, crash should have failed
        assert results.get("ok1") == "completed"
        assert results.get("ok2") == "completed"
        assert manager.supervisors["crash"].health.total_errors >= 1

        await manager.stop_all()


class TestSupervisorConfig:
    """Tests for SupervisorConfig."""

    def test_default_values(self):
        """Config should have sensible defaults."""
        config = SupervisorConfig()
        assert config.max_restarts == 5
        assert config.backoff_base_sec == 10.0
        assert config.backoff_max_sec == 300.0
        assert config.jitter is True

    def test_custom_values(self):
        """Config should accept custom values."""
        config = SupervisorConfig(
            max_restarts=3,
            backoff_base_sec=5.0,
        )
        assert config.max_restarts == 3
        assert config.backoff_base_sec == 5.0
