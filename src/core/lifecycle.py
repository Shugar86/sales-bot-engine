"""
Lifecycle Management — Task Supervision and Restart

Provides structured lifecycle management for persona tasks:
- Task supervision with restart policies
- Graceful shutdown handling
- Health tracking (last_alive, restart count)
- Structured logging of lifecycle events
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Awaitable, Optional, Tuple

from ..utils.logger import get_logger

logger = get_logger("lifecycle")


class TaskState(Enum):
    """State of a supervised task."""
    PENDING = "pending"
    RUNNING = "running"
    RESTARTING = "restarting"
    STOPPED = "stopped"
    FAILED = "failed"
    EXHAUSTED = "exhausted"  # Max restarts reached


@dataclass
class SupervisorConfig:
    """Configuration for task supervision."""

    #: After this many *restarts* (not counting the first run), give up → FAILED.
    #: Total task attempts = max_restarts + 1 (e.g. max_restarts=2 → 3 crashes then stop).
    max_restarts: int = 5
    backoff_base_sec: float = 10.0
    backoff_max_sec: float = 300.0  # 5 minutes max
    jitter: bool = True
    graceful_shutdown_timeout_sec: float = 10.0
    health_check_interval_sec: float = 30.0
    #: If non-empty, used instead of exponential backoff: delay before restart after
    #: failure on attempt ``i`` is ``backoff_schedule_sec[min(i, len-1)]``.
    backoff_schedule_sec: Tuple[float, ...] = field(default_factory=tuple)


@dataclass
class TaskHealth:
    """Health metrics for a supervised task."""
    state: TaskState = TaskState.PENDING
    started_at: Optional[float] = None
    last_alive_at: Optional[float] = None
    restart_count: int = 0
    consecutive_errors: int = 0
    total_errors: int = 0
    last_error: Optional[str] = None
    last_error_at: Optional[float] = None


class PersonaSupervisor:
    """
    Supervises a single persona task with automatic restart.

    Features:
    - Exponential backoff with jitter
    - Health tracking (last_alive, error counts)
    - Graceful cancellation support
    - Structured logging with persona context
    """

    def __init__(
        self,
        persona_name: str,
        task_factory: Callable[[], Awaitable[None]],
        config: Optional[SupervisorConfig] = None,
    ):
        """
        Args:
            persona_name: Name of the persona (for logging)
            task_factory: Async function that runs the persona
            config: Supervisor configuration
        """
        self.persona_name = persona_name
        self.task_factory = task_factory
        self.config = config or SupervisorConfig()
        self.health = TaskHealth()

        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._health_check_task: Optional[asyncio.Task] = None

    def _log(self, level: str, message: str, **kwargs):
        """Log with persona context."""
        extra = {
            "persona_name": self.persona_name,
            "restart_count": self.health.restart_count,
            "state": self.health.state.value,
            **kwargs,
        }
        getattr(logger, level)(f"[{self.persona_name}] {message}", extra=extra)

    def _calculate_backoff(self, attempt: int) -> float:
        """Calculate backoff delay before the next restart after failure on ``attempt``."""
        import random

        sched = self.config.backoff_schedule_sec
        if sched:
            idx = min(attempt, len(sched) - 1)
            return max(0.0, float(sched[idx]))

        delay = self.config.backoff_base_sec * (2 ** attempt)
        delay = min(delay, self.config.backoff_max_sec)

        if self.config.jitter:
            jitter = delay * 0.25 * (2 * random.random() - 1)
            delay += jitter

        return max(0.0, delay)

    async def _run_with_health_check(self) -> None:
        """Run the task with health check monitoring."""
        self.health.state = TaskState.RUNNING
        self.health.started_at = time.time()
        self.health.last_alive_at = time.time()

        try:
            await self.task_factory()
        finally:
            self.health.state = TaskState.STOPPED

    async def _supervise(self) -> None:
        """Main supervision loop."""
        self._log("info", "Supervisor starting")

        for attempt in range(self.config.max_restarts + 1):
            if self._stop_event.is_set():
                self._log("info", "Stop requested, exiting supervision loop")
                break

            try:
                self.health.state = TaskState.RUNNING
                self._log("info", f"Starting task (attempt {attempt + 1})")

                await self._run_with_health_check()

                # Clean exit - don't restart
                self._log("info", "Task exited cleanly")
                self.health.state = TaskState.STOPPED
                return

            except asyncio.CancelledError:
                self._log("info", "Task cancelled")
                self.health.state = TaskState.STOPPED
                raise  # Re-raise to propagate cancellation

            except Exception as e:
                self.health.total_errors += 1
                self.health.consecutive_errors += 1
                self.health.last_error = str(e)
                self.health.last_error_at = time.time()

                if attempt < self.config.max_restarts:
                    backoff = self._calculate_backoff(attempt)
                    self.health.restart_count += 1
                    self.health.state = TaskState.RESTARTING

                    self._log(
                        "warning",
                        f"Task crashed: {e}. Restarting in {backoff:.1f}s "
                        f"(attempt {self.health.restart_count}/{self.config.max_restarts})"
                    )

                    # Wait for backoff or stop signal
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(),
                            timeout=backoff
                        )
                        # If we get here, stop was requested
                        self._log("info", "Stop requested during backoff, aborting restart")
                        break
                    except asyncio.TimeoutError:
                        # Backoff elapsed, continue to restart
                        pass
                else:
                    self.health.state = TaskState.FAILED
                    self._log(
                        "error",
                        f"Task FAILED after {self.config.max_restarts + 1} attempts "
                        f"(no more restarts). Final error: {e}"
                    )
                    return

    async def start(self) -> None:
        """Start supervision."""
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._supervise(),
            name=f"supervisor-{self.persona_name}"
        )

    async def stop(self, timeout: Optional[float] = None) -> None:
        """
        Stop supervision gracefully.

        Args:
            timeout: How long to wait for task to stop (default: config.graceful_shutdown_timeout_sec)
        """
        timeout = timeout or self.config.graceful_shutdown_timeout_sec

        self._log("info", f"Stopping supervisor (timeout={timeout}s)")
        self._stop_event.set()

        if self._task and not self._task.done():
            self._task.cancel()

            try:
                await asyncio.wait_for(self._task, timeout=timeout)
                self._log("info", "Task stopped gracefully")
            except asyncio.TimeoutError:
                self._log("warning", f"Task did not stop within {timeout}s")
            except asyncio.CancelledError:
                pass

        self.health.state = TaskState.STOPPED

    def is_running(self) -> bool:
        """Check if the supervised task is currently running."""
        return (
            self._task is not None and
            not self._task.done() and
            self.health.state in (TaskState.RUNNING, TaskState.RESTARTING)
        )

    def get_health(self) -> dict:
        """Get current health status as dict."""
        now = time.time()

        state_val = self.health.state.value
        if self.health.state == TaskState.EXHAUSTED:
            state_val = "failed"

        return {
            "persona_name": self.persona_name,
            "state": state_val,
            "is_running": self.is_running(),
            "started_at": self.health.started_at,
            "uptime_sec": now - self.health.started_at if self.health.started_at else None,
            "last_alive_sec_ago": now - self.health.last_alive_at if self.health.last_alive_at else None,
            "restart_count": self.health.restart_count,
            "consecutive_errors": self.health.consecutive_errors,
            "total_errors": self.health.total_errors,
            "last_error": self.health.last_error,
            "last_error_at": self.health.last_error_at,
        }


class LifecycleManager:
    """
    Manages multiple persona supervisors.

    Coordinates graceful shutdown of all personas and provides
    aggregated health status.
    """

    def __init__(self):
        self.supervisors: dict[str, PersonaSupervisor] = {}
        self._shutdown_event = asyncio.Event()

    def register(self, name: str, supervisor: PersonaSupervisor) -> None:
        """Register a supervisor."""
        self.supervisors[name] = supervisor

    async def start_all(self) -> None:
        """Start all supervisors."""
        logger.info(f"Starting {len(self.supervisors)} supervisors")

        for name, supervisor in self.supervisors.items():
            await supervisor.start()

    async def stop_all(self, timeout: float = 10.0) -> None:
        """
        Stop all supervisors gracefully.

        Args:
            timeout: Timeout per supervisor
        """
        logger.info(f"Stopping {len(self.supervisors)} supervisors")
        self._shutdown_event.set()

        # Stop all in parallel
        stops = [
            supervisor.stop(timeout=timeout)
            for supervisor in self.supervisors.values()
        ]

        if stops:
            await asyncio.gather(*stops, return_exceptions=True)

        logger.info("All supervisors stopped")

    def get_health(self) -> dict:
        """Get health status for all supervisors."""
        return {
            name: supervisor.get_health()
            for name, supervisor in self.supervisors.items()
        }

    async def wait_for_shutdown(self) -> None:
        """Wait until shutdown is requested or all tasks complete."""
        if not self.supervisors:
            await self._shutdown_event.wait()
            return

        # Wait for any supervisor to complete (they restart internally)
        tasks = [
            s._task for s in self.supervisors.values()
            if s._task and not s._task.done()
        ]

        if not tasks:
            await self._shutdown_event.wait()
            return

        done, _ = await asyncio.wait(
            tasks,
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Log which task completed
        for task in done:
            exc = task.exception()
            if exc:
                logger.warning(f"Supervisor task exited with error: {exc}")


# Backward-compatible helper for orchestrator
async def run_with_restart(
    persona_name: str,
    task_factory: Callable[[], Awaitable[None]],
    max_restarts: int = 5,
    backoff_base_sec: float = 10.0,
) -> None:
    """
    Simple helper to run a task with restart.

    This is a convenience wrapper around PersonaSupervisor for simple use cases.

    Args:
        persona_name: Name for logging
        task_factory: Async function to run
        max_restarts: Maximum restart attempts
        backoff_base_sec: Base backoff in seconds
    """
    config = SupervisorConfig(
        max_restarts=max_restarts,
        backoff_base_sec=backoff_base_sec,
    )
    supervisor = PersonaSupervisor(persona_name, task_factory, config)
    await supervisor._supervise()
