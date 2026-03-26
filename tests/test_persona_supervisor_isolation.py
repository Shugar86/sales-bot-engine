"""Supervisor isolation: one persona exhausts restarts → FAILED; others keep running."""

import asyncio

import pytest

from src.core.lifecycle import (
    LifecycleManager,
    PersonaSupervisor,
    SupervisorConfig,
    TaskState,
)


@pytest.mark.asyncio
async def test_one_supervisor_failed_others_still_running() -> None:
    """After max restarts one persona is FAILED; another supervisor remains active."""
    manager = LifecycleManager()

    async def always_crash() -> None:
        raise ValueError("always fails")

    async def long_running() -> None:
        await asyncio.sleep(300)

    bad = PersonaSupervisor(
        "failing",
        always_crash,
        SupervisorConfig(
            max_restarts=2,
            backoff_schedule_sec=(0.01, 0.01, 0.01),
            jitter=False,
        ),
    )
    good = PersonaSupervisor(
        "healthy",
        long_running,
        SupervisorConfig(max_restarts=2, backoff_base_sec=0.01, jitter=False),
    )
    manager.register("failing", bad)
    manager.register("healthy", good)

    await manager.start_all()

    # Wait until failing supervisor finishes its supervision loop (3 attempts).
    assert bad._task is not None
    await asyncio.wait_for(bad._task, timeout=5.0)

    assert bad.health.state == TaskState.FAILED
    assert bad.get_health()["state"] == "failed"
    assert good.is_running()

    await manager.stop_all(timeout=1.0)
    assert not good.is_running()
