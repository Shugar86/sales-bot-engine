#!/usr/bin/env python3
"""Static health checks for sales-bot-engine (no HTTP server).

Reads configuration from the environment (same conventions as the main app).
Prints JSON to stdout. Exit code 0 if critical checks pass; 1 if personas or
Postgres (when ``DATABASE_URL`` is set) fail. LLM reachability is optional: a
failure is reported as degraded in JSON but does not force exit 1.

Environment variables:
    PERSONAS_DIR: Directory with persona folders (default: ./personas)
    DATABASE_URL: If set, Postgres is pinged via asyncpg
    OPENROUTER_API_KEY: If set, OpenRouter models endpoint is probed
    MEMORY_DIR: Directory that should be writable (default: ./data/memory)
    SALES_BOT_HEALTH_FILE: Optional JSON snapshot to embed under ``snapshot``
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

# Repo root (parent of scripts/)
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _load_optional_snapshot(path: Path) -> Optional[dict[str, Any]]:
    """Read optional health snapshot JSON if the file exists."""
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


async def _check_postgres(database_url: str, timeout_sec: float = 3.0) -> dict[str, Any]:
    """Return status dict for Postgres connectivity."""
    import asyncpg

    conn: Any = None
    try:
        conn = await asyncio.wait_for(asyncpg.connect(database_url), timeout=timeout_sec)
        await conn.execute("SELECT 1")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        if conn is not None:
            await conn.close()


def _check_personas(personas_dir: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Load each ``persona.yaml`` under subdirectories; return rows and error messages."""
    from src.core.persona_manager import load_persona

    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    root = Path(personas_dir)
    if not root.is_dir():
        errors.append(f"personas_dir is not a directory: {personas_dir}")
        return rows, errors

    for subdir in sorted(root.iterdir()):
        if not subdir.is_dir():
            continue
        yaml_file = subdir / "persona.yaml"
        if not yaml_file.is_file():
            continue
        try:
            cfg = load_persona(str(yaml_file))
            rows.append({"name": cfg.name, "ok": True, "path": str(yaml_file)})
        except Exception as e:
            errors.append(f"{yaml_file}: {e}")
            rows.append({"name": subdir.name, "ok": False, "error": str(e)})
    return rows, errors


def _check_memory_dir(memory_dir: str) -> dict[str, Any]:
    """Verify memory directory exists or can be created and is writable."""
    try:
        path = Path(memory_dir)
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".health_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return {"ok": True, "path": str(path.resolve())}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def _run_checks(
    personas_dir: str,
    memory_dir: str,
    database_url: str,
    api_key: str,
    snapshot_path: Path,
) -> dict[str, Any]:
    """Execute all probes and assemble the report dict."""
    from src.core.health import check_llm_reachable

    persona_rows, persona_errors = _check_personas(personas_dir)
    memory_status = _check_memory_dir(memory_dir)

    report: dict[str, Any] = {
        "personas_dir": personas_dir,
        "personas": persona_rows,
        "memory": memory_status,
        "postgres": None,
        "llm": None,
        "snapshot": _load_optional_snapshot(snapshot_path),
        "critical_ok": True,
        "warnings": [],
    }

    if persona_errors:
        report["critical_ok"] = False
        report["persona_errors"] = persona_errors

    if not memory_status.get("ok"):
        report["critical_ok"] = False

    if database_url.strip():
        pg = await _check_postgres(database_url.strip())
        report["postgres"] = pg
        if not pg.get("ok"):
            report["critical_ok"] = False
    else:
        report["postgres"] = {"ok": None, "detail": "DATABASE_URL not set"}

    if api_key.strip():
        llm = await check_llm_reachable(api_key.strip())
        report["llm"] = {
            "ok": llm.status.value == "healthy",
            "status": llm.status.value,
            "details": llm.details,
            "latency_ms": llm.latency_ms,
        }
        if not report["llm"]["ok"]:
            report["warnings"].append("LLM probe failed (non-fatal for exit code)")
    else:
        report["llm"] = {"ok": None, "detail": "OPENROUTER_API_KEY not set"}

    return report


def _print_table(report: dict[str, Any]) -> None:
    """Human-readable summary on stdout (secondary to JSON in machine workflows)."""
    print(f"personas_dir: {report.get('personas_dir')}")
    print(f"critical_ok: {report.get('critical_ok')}")
    for p in report.get("personas") or []:
        status = "OK" if p.get("ok") else "FAIL"
        print(f"  [{status}] {p.get('name')}")
    mem = report.get("memory") or {}
    print(f"memory: {'OK' if mem.get('ok') else 'FAIL'} {mem.get('path') or mem.get('error')}")
    pg = report.get("postgres") or {}
    print(f"postgres: {pg}")
    llm = report.get("llm") or {}
    print(f"llm: {llm}")
    for w in report.get("warnings") or []:
        print(f"warning: {w}")


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Run static health checks (personas, Postgres, LLM, memory dir)."
    )
    parser.add_argument(
        "--format",
        choices=("json", "table"),
        default="json",
        help="json (default) or table",
    )
    args = parser.parse_args()

    personas_dir = os.getenv("PERSONAS_DIR", "./personas")
    memory_dir = os.getenv("MEMORY_DIR", "./data/memory")
    database_url = os.getenv("DATABASE_URL", "")
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    snapshot_file = os.getenv("SALES_BOT_HEALTH_FILE", "/tmp/sales-bot-health.json")

    report = asyncio.run(
        _run_checks(
            personas_dir=personas_dir,
            memory_dir=memory_dir,
            database_url=database_url,
            api_key=api_key,
            snapshot_path=Path(snapshot_file),
        )
    )

    if args.format == "table":
        _print_table(report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    return 0 if report.get("critical_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
