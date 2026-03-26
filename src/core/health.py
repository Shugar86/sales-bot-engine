"""
Health Monitoring — Production health checks and status reporting.

Provides:
- Health status for all personas
- System-level health probes
- Health file for external monitoring (Docker, load balancers)
- Periodic health reporting
"""

import asyncio
import json
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, Any, Optional, Callable, Awaitable

from ..utils.logger import get_logger

logger = get_logger("health")


class HealthStatus(Enum):
    """Health status levels."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class HealthCheckResult:
    """Result of a health check."""
    name: str
    status: HealthStatus
    details: Dict[str, Any]
    checked_at: float
    latency_ms: float


class HealthReporter:
    """
    Reports health status for the entire system.

    Writes health status to a JSON file for external monitoring systems
    (Docker healthcheck, load balancers, monitoring dashboards).
    """

    def __init__(
        self,
        output_path: str = "/tmp/sales-bot-health.json",
        write_interval_sec: float = 30.0,
    ):
        self.output_path = Path(output_path)
        self.write_interval_sec = write_interval_sec
        self._health_data: Dict[str, Any] = {
            "status": HealthStatus.UNHEALTHY.value,
            "timestamp": time.time(),
            "checks": {},
        }
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def update(self, data: Dict[str, Any]):
        """Update health data."""
        self._health_data.update(data)
        self._health_data["timestamp"] = time.time()

    def _write_health_file(self):
        """Write health data to file atomically."""
        try:
            tmp_path = self.output_path.with_suffix(".tmp")
            with open(tmp_path, "w") as f:
                json.dump(self._health_data, f, indent=2)
            tmp_path.replace(self.output_path)
        except Exception as e:
            logger.error(f"Failed to write health file: {e}")

    async def _report_loop(self):
        """Background loop to write health file periodically."""
        while self._running:
            self._write_health_file()
            try:
                await asyncio.wait_for(
                    asyncio.sleep(self.write_interval_sec),
                    timeout=self.write_interval_sec + 5,
                )
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break

    def start(self):
        """Start background reporting."""
        self._running = True
        self._task = asyncio.create_task(self._report_loop())
        logger.info(f"Health reporter started, writing to {self.output_path}")

    async def stop(self):
        """Stop background reporting."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Final write
        self._write_health_file()
        logger.info("Health reporter stopped")

    def is_healthy(self) -> bool:
        """Check if system is healthy based on current data."""
        return self._health_data.get("status") == HealthStatus.HEALTHY.value


class HealthChecker:
    """
    Performs health checks on system components.
    """

    def __init__(self, reporter: Optional[HealthReporter] = None):
        self.reporter = reporter or HealthReporter()
        self._checkers: Dict[str, Callable[[], Awaitable[HealthCheckResult]]] = {}

    def register(
        self,
        name: str,
        checker: Callable[[], Awaitable[HealthCheckResult]],
    ):
        """Register a health check function."""
        self._checkers[name] = checker

    async def check_all(self) -> Dict[str, HealthCheckResult]:
        """Run all registered health checks."""
        results = {}
        for name, checker in self._checkers.items():
            start = time.time()
            try:
                result = await checker()
            except Exception as e:
                result = HealthCheckResult(
                    name=name,
                    status=HealthStatus.UNHEALTHY,
                    details={"error": str(e)},
                    checked_at=time.time(),
                    latency_ms=(time.time() - start) * 1000,
                )
            results[name] = result
        return results

    async def run_checks_and_report(self):
        """Run all checks and update reporter."""
        results = await self.check_all()

        # Determine overall status
        statuses = [r.status for r in results.values()]
        if HealthStatus.UNHEALTHY in statuses:
            overall = HealthStatus.UNHEALTHY
        elif HealthStatus.DEGRADED in statuses:
            overall = HealthStatus.DEGRADED
        else:
            overall = HealthStatus.HEALTHY

        # Update reporter
        self.reporter.update({
            "status": overall.value,
            "checks": {
                name: {
                    "status": r.status.value,
                    "details": r.details,
                    "latency_ms": r.latency_ms,
                    "checked_at": r.checked_at,
                }
                for name, r in results.items()
            },
        })

        return overall, results

    def start(self):
        """Start the health reporter."""
        self.reporter.start()

    async def stop(self):
        """Stop the health reporter."""
        await self.reporter.stop()


# ═══════════════════════════════════════════════════════════════════════════════
# Built-in health check functions
# ═══════════════════════════════════════════════════════════════════════════════

async def check_llm_reachable(
    api_key: str,
    timeout: float = 10.0,
) -> HealthCheckResult:
    """Check if LLM API is reachable."""
    import httpx

    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            latency_ms = (time.time() - start) * 1000

            if response.status_code == 200:
                return HealthCheckResult(
                    name="llm_api",
                    status=HealthStatus.HEALTHY,
                    details={"latency_ms": latency_ms},
                    checked_at=time.time(),
                    latency_ms=latency_ms,
                )
            else:
                return HealthCheckResult(
                    name="llm_api",
                    status=HealthStatus.UNHEALTHY,
                    details={"status_code": response.status_code, "latency_ms": latency_ms},
                    checked_at=time.time(),
                    latency_ms=latency_ms,
                )
    except Exception as e:
        latency_ms = (time.time() - start) * 1000
        return HealthCheckResult(
            name="llm_api",
            status=HealthStatus.UNHEALTHY,
            details={"error": str(e)},
            checked_at=time.time(),
            latency_ms=latency_ms,
        )


async def check_memory_writable(memory_dir: str) -> HealthCheckResult:
    """Check if memory directory is writable."""
    start = time.time()
    try:
        path = Path(memory_dir)
        path.mkdir(parents=True, exist_ok=True)

        # Test write
        test_file = path / ".health_check_test"
        test_file.write_text(str(time.time()))
        test_file.unlink()

        latency_ms = (time.time() - start) * 1000
        return HealthCheckResult(
            name="memory_writable",
            status=HealthStatus.HEALTHY,
            details={"path": str(path)},
            checked_at=time.time(),
            latency_ms=latency_ms,
        )
    except Exception as e:
        latency_ms = (time.time() - start) * 1000
        return HealthCheckResult(
            name="memory_writable",
            status=HealthStatus.UNHEALTHY,
            details={"error": str(e)},
            checked_at=time.time(),
            latency_ms=latency_ms,
        )


async def check_personas_loaded(personas_dir: str) -> HealthCheckResult:
    """Check if personas directory exists and has persona configs."""
    start = time.time()
    try:
        path = Path(personas_dir)
        if not path.exists():
            return HealthCheckResult(
                name="personas",
                status=HealthStatus.UNHEALTHY,
                details={"error": f"Directory not found: {path}"},
                checked_at=time.time(),
                latency_ms=(time.time() - start) * 1000,
            )

        persona_files = list(path.rglob("persona.yaml"))
        latency_ms = (time.time() - start) * 1000

        return HealthCheckResult(
            name="personas",
            status=HealthStatus.HEALTHY,
            details={
                "count": len(persona_files),
                "path": str(path),
                "personas": [p.parent.name for p in persona_files],
            },
            checked_at=time.time(),
            latency_ms=latency_ms,
        )
    except Exception as e:
        latency_ms = (time.time() - start) * 1000
        return HealthCheckResult(
            name="personas",
            status=HealthStatus.UNHEALTHY,
            details={"error": str(e)},
            checked_at=time.time(),
            latency_ms=latency_ms,
        )


def create_default_health_checker(
    api_key: str,
    memory_dir: str,
    personas_dir: str,
) -> HealthChecker:
    """Create a health checker with default checks."""
    checker = HealthChecker()

    checker.register("llm_api", lambda: check_llm_reachable(api_key))
    checker.register("memory_writable", lambda: check_memory_writable(memory_dir))
    checker.register("personas", lambda: check_personas_loaded(personas_dir))

    return checker


# ═══════════════════════════════════════════════════════════════════════════════
# CLI health probe script support
# ═══════════════════════════════════════════════════════════════════════════════

async def health_probe() -> int:
    """
    CLI entry point for health probe.

    Returns exit code 0 if healthy, 1 otherwise.
    Used by Docker healthcheck.
    """

    health_file = Path("/tmp/sales-bot-health.json")

    if not health_file.exists():
        print("Health file not found")
        return 1

    try:
        data = json.loads(health_file.read_text())
        status = data.get("status", "unknown")

        if status == HealthStatus.HEALTHY.value:
            print(f"Status: {status}")
            return 0
        else:
            print(f"Status: {status}")
            if "checks" in data:
                for name, check in data["checks"].items():
                    print(f"  {name}: {check.get('status')}")
            return 1
    except Exception as e:
        print(f"Error reading health file: {e}")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(health_probe())
    exit(exit_code)
