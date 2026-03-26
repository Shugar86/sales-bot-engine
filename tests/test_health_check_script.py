"""Smoke tests for ``scripts/health_check.py`` (no live Postgres required)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "health_check.py"


def _minimal_persona_dir(base: Path) -> Path:
    """Create ``base/<name>/persona.yaml`` suitable for :func:`load_persona`."""
    p_dir = base / "demo_bot"
    p_dir.mkdir(parents=True)
    data = {
        "persona": {
            "name": "DemoBot",
            "platform": "telegram",
            "account_type": "userbot",
            "session_name": "demo",
            "api_id": 12345,
            "api_hash": "testhash",
            "phone": "+79001234567",
            "personality": "Unit test bot",
            "groups_to_monitor": ["-100123"],
            "product": {
                "name": "Test Product",
                "price": "100₽",
                "link": "https://test.com",
            },
            "triggers": {
                "respond_when": [{"keywords": ["тест"]}],
                "ignore_when": [{"contains": ["спам"], "from_bot": True}],
            },
            "conversation_flow": {
                "group_mode": {"max_messages_per_hour": 10, "style": "дружелюбный"},
                "dm_mode": {
                    "greeting": "Привет!",
                    "funnel": [{"step": "помочь"}],
                },
            },
            "anti_spam": {
                "min_delay_between_messages": 0,
                "max_delay_between_messages": 0,
                "typing_simulation": False,
                "random_typos": False,
            },
            "router_model": "openrouter/test-fast",
            "generator_model": "openrouter/test-slow",
        }
    }
    with open(p_dir / "persona.yaml", "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True)
    return base


@pytest.mark.parametrize("fmt", ("json", "table"))
def test_health_check_exits_zero_without_database(tmp_path: Path, fmt: str) -> None:
    """Script should return 0 when personas and memory dir are OK and DB is unset."""
    personas_root = _minimal_persona_dir(tmp_path / "personas")
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()

    env = os.environ.copy()
    env["PYTHONPATH"] = str(_REPO_ROOT)
    env["PERSONAS_DIR"] = str(personas_root)
    env["MEMORY_DIR"] = str(memory_dir)
    env.pop("DATABASE_URL", None)
    env.pop("OPENROUTER_API_KEY", None)

    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "--format", fmt],
        env=env,
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    if fmt == "json":
        report = json.loads(proc.stdout)
        assert report["critical_ok"] is True
        assert len(report["personas"]) == 1
        assert report["personas"][0]["ok"] is True
        assert report["memory"]["ok"] is True
