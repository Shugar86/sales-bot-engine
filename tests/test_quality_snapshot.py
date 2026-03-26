"""quality_snapshot script: structure and mock mode."""

import json
import os
import subprocess
import sys
from pathlib import Path


def test_quality_snapshot_mock_subprocess(tmp_path) -> None:
    """Mock run produces JSON with cases and PASS/WARN/FAIL summary."""
    repo = Path(__file__).resolve().parents[1]
    personas = tmp_path / "personas" / "demo_fit"
    personas.mkdir(parents=True)
    yaml_text = """persona:
  name: Demo Fit
  platform: telegram
  account_type: userbot
  personality: "Консультант"
  competitor_knowledge: "Пример"
  group_context_examples: []
  response_examples:
    - trigger: "Нужен корм"
      good_response: "Смотрите состав"
      bad_response: "Покупайте всё"
  triggers:
    respond_when: []
    ignore_when: []
  conversation_flow:
    group_mode: {max_messages_per_hour: 3, style: "спокойный"}
    dm_mode: {greeting: "", funnel: []}
quality_test_cases:
  - "Как подобрать корм для собаки?"
"""
    (personas / "persona.yaml").write_text(yaml_text, encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo)
    proc = subprocess.run(
        [
            sys.executable,
            str(repo / "scripts" / "quality_snapshot.py"),
            "--persona",
            "demo_fit",
            "--personas-dir",
            str(tmp_path / "personas"),
            "--mock",
        ],
        cwd=str(repo),
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data.get("mock") is True
    assert "summary" in data and "cases" in data
    assert data["summary"]["PASS"] + data["summary"]["WARN"] + data["summary"]["FAIL"] == len(
        data["cases"]
    )
    assert data["cases"][0]["status"] in ("PASS", "WARN", "FAIL")
