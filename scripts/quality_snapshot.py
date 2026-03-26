#!/usr/bin/env python3
"""Run quick DM quality checks against a persona YAML (optional mocked LLM)."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, List

if TYPE_CHECKING:
    from src.core.persona_manager import PersonaConfig
    from src.responders.generator import ResponseGenerator
    from src.utils.llm_client import LLMClient

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _resolve_persona_yaml(personas_dir: Path, query: str) -> Path:
    """Return ``persona.yaml`` path for a folder slug or persona ``name``."""
    import yaml

    q = query.strip().lower()
    if not personas_dir.is_dir():
        raise FileNotFoundError(f"personas dir missing: {personas_dir}")
    for sub in sorted(personas_dir.iterdir()):
        if not sub.is_dir():
            continue
        y = sub / "persona.yaml"
        if not y.is_file():
            continue
        if sub.name.lower() == q:
            return y
        try:
            raw = yaml.safe_load(y.read_text(encoding="utf-8")) or {}
            pdata = raw.get("persona", raw)
            name = str(pdata.get("name", "")).strip().lower()
            if name == q:
                return y
        except (OSError, yaml.YAMLError):
            continue
    raise FileNotFoundError(f"Persona not found: {query!r} under {personas_dir}")


def _build_dm_generator(cfg: PersonaConfig, llm: LLMClient) -> ResponseGenerator:
    """Mirror orchestrator wiring for DM generation prompts."""
    from src.core.orchestrator import SalesBotOrchestrator
    from src.core.prompt_compiler import PromptCompiler
    from src.core.vibe_schema import ResponseExample
    from src.responders.generator import ResponseGenerator

    orch = SalesBotOrchestrator(openrouter_api_key="quality-snapshot")
    contract = orch._build_router_contract(cfg)
    prompt_compiler = PromptCompiler(
        vibe=cfg.vibe,
        behavior=cfg.behavior,
        response_examples=[
            ResponseExample(
                trigger=ex.trigger,
                bad_response=ex.bad_response,
                good_response=ex.good_response,
            )
            for ex in (cfg.response_examples or [])
        ],
        competitor_knowledge=cfg.competitor_knowledge or "",
        personality=cfg.personality or "",
    )
    behavior_block = prompt_compiler.compile_system_prompt()
    ex_list: List[dict[str, str]] | None = None
    if cfg.response_examples:
        ex_list = [
            {"trigger": ex.trigger, "bad": ex.bad_response, "good": ex.good_response}
            for ex in cfg.response_examples
        ]
    return ResponseGenerator(
        llm_client=llm,
        model=cfg.generator_model,
        contract=contract,
        response_examples=ex_list or [],
        behavior_block=behavior_block,
    )


def _collect_questions(raw_yaml: dict[str, Any], cfg: PersonaConfig) -> List[str]:
    """Prefer ``quality_test_cases``; else a few ``response_examples`` triggers; else defaults."""
    q = raw_yaml.get("quality_test_cases")
    if q is None and isinstance(raw_yaml.get("persona"), dict):
        q = raw_yaml["persona"].get("quality_test_cases")
    if isinstance(q, list) and q:
        return [str(x).strip() for x in q if str(x).strip()]
    if cfg.response_examples:
        return [ex.trigger for ex in cfg.response_examples[:5] if ex.trigger]
    return [
        "Какой корм лучше для взрослой собаки средних пород?",
        "Собака чешется после прогулки — что может быть?",
    ]


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", (text or "").strip()))


def _evaluate_reply(text: str, taboos: List[str]) -> dict[str, Any]:
    """Return status PASS | WARN | FAIL and human-readable issues."""
    from src.responders.generator import _clean_user_visible_text, _strip_markdown_fences

    stripped = (text or "").strip()
    issues: List[str] = []
    if not stripped:
        return {"status": "FAIL", "issues": ["empty_reply"], "word_count": 0}

    wc = _word_count(stripped)
    status = "PASS"
    if wc < 5 or wc > 200:
        issues.append(f"word_count={wc} (expected roughly 5–200)")
        status = "WARN"

    normalized = _clean_user_visible_text(_strip_markdown_fences(stripped))
    if re.search(r'"text"\s*:', stripped, re.IGNORECASE):
        issues.append("json_text_key_leak")
        status = "FAIL"
    if stripped.lstrip().startswith("{") and "}" in stripped:
        issues.append("json_object_like_reply")
        status = "FAIL"
    if stripped != normalized and re.search(r"[{}]", stripped):
        issues.append("brace_artifact")
        if status == "PASS":
            status = "WARN"

    low = stripped.lower()
    for tab in taboos or []:
        t = (tab or "").strip().lower()
        if t and t in low:
            issues.append(f"taboo_hit:{tab}")
            status = "FAIL"

    return {"status": status, "issues": issues, "word_count": wc}


async def run_quality(args: argparse.Namespace) -> dict[str, Any]:
    """Execute checks and return a JSON-serializable report."""
    import yaml
    from unittest.mock import AsyncMock

    from src.core.persona_manager import load_persona
    from src.utils.llm_client import LLMClient, LLMResponse

    personas_dir = Path(args.personas_dir)
    yaml_path = _resolve_persona_yaml(personas_dir, args.persona)
    cfg = load_persona(str(yaml_path))
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    questions = _collect_questions(raw, cfg)
    taboos: List[str] = list(cfg.vibe.taboos) if cfg.vibe and cfg.vibe.taboos else []

    api_key = (args.api_key or os.getenv("OPENROUTER_API_KEY") or "").strip()
    llm = LLMClient(api_key=api_key or "mock-key")
    if args.mock:
        llm.call = AsyncMock(
            return_value=LLMResponse(
                text=(
                    "Привет, для взрослой собаки смотри на белок и жиры в составе, "
                    "и если есть аллергия — на злаки. Могу подсказать конкретные линейки."
                ),
                model="mock",
                success=True,
            )
        )

    generator = _build_dm_generator(cfg, llm)
    cases: List[dict[str, Any]] = []
    for question in questions:
        try:
            gr = await generator.generate_dm_response(
                question,
                user_memory="",
                dm_history="(снимок качества)",
                funnel_stage="engage",
            )
        except Exception as e:
            cases.append(
                {
                    "question": question,
                    "status": "FAIL",
                    "issues": [f"exception:{e}"],
                    "reply_preview": "",
                }
            )
            continue
        reply = gr.text if gr else ""
        ev = _evaluate_reply(reply, taboos)
        cases.append(
            {
                "question": question,
                "status": ev["status"],
                "issues": ev["issues"],
                "word_count": ev["word_count"],
                "reply_preview": reply[:240],
            }
        )

    summary = {"PASS": 0, "WARN": 0, "FAIL": 0}
    for c in cases:
        summary[c["status"]] = summary.get(c["status"], 0) + 1

    return {
        "persona": cfg.name,
        "yaml": str(yaml_path),
        "mock": bool(args.mock),
        "summary": summary,
        "cases": cases,
    }


def main() -> int:
    """CLI entry."""
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(description="DM quality snapshot for one persona.")
    parser.add_argument(
        "--persona",
        required=True,
        help="Persona folder slug or YAML ``name``",
    )
    parser.add_argument(
        "--personas-dir",
        default=os.getenv("PERSONAS_DIR", "./personas"),
        help="Directory containing persona subfolders",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Do not call the network; stub llm.call",
    )
    parser.add_argument(
        "--api-key",
        default="",
        help="Override OPENROUTER_API_KEY for non-mock runs",
    )
    args = parser.parse_args()
    report = asyncio.run(run_quality(args))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
