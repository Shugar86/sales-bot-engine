"""Static checks for persona YAML files before runtime."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Tuple

from .persona_manager import PersonaConfig, load_persona

# (regex, human-readable label for error messages)
_SECRET_PATTERNS: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"\+7\s*\d{3}\s*\d{3}\s*\d{2}\s*\d{2}"), "phone number (+7…)"),
    (re.compile(r"sk-or-v1-[A-Za-z0-9_-]{20,}"), "OpenRouter API key pattern"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}"), "JWT-like secret"),
]

# Hints that examples cover «human vs bot» style (WARNING if none match)
_BOT_OR_AI_MARKERS: tuple[str, ...] = (
    "бот",
    "gpt",
    "нейросет",
    "openai",
    "chatgpt",
    "чат-гпт",
    " ии",
    "ии ",
    "artificial",
)


def _example_text_blobs(config: PersonaConfig) -> list[str]:
    """Concatenate trigger / good / bad fields from response and group examples."""
    parts: list[str] = []
    for ex in config.response_examples:
        parts.extend([ex.trigger, ex.bad_response, ex.good_response])
    for ex in config.group_context_examples:
        parts.extend([ex.trigger, ex.bad_response, ex.good_response])
    return parts


def _has_bot_or_ai_cue(config: PersonaConfig) -> bool:
    """True if any example text mentions bots / AI (Turing-style coverage)."""
    blob = " ".join(t.lower() for t in _example_text_blobs(config) if t)
    return any(m in blob for m in _BOT_OR_AI_MARKERS)


def collect_persona_semantic_issues(config: PersonaConfig) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for a loaded :class:`PersonaConfig`.

    Errors fail CI via :func:`assert_persona_yaml_file_valid`. Warnings are printed to stderr.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if len(config.response_examples) < 3:
        errors.append("need at least 3 response_examples")

    if not config.respond_triggers:
        errors.append("triggers.respond_when must not be empty")

    mph = config.group_mode.max_messages_per_hour
    if mph < 1 or mph > 10:
        errors.append(f"group_mode.max_messages_per_hour must be 1–10, got {mph}")

    if config.vibe is not None:
        back = (config.vibe.backstory or "").strip()
        if len(back) < 50:
            errors.append("vibe.backstory must be at least 50 characters when vibe is set")
        taboos = config.vibe.taboos or []
        if not taboos:
            errors.append("vibe.taboos must not be empty when vibe is set")

    if not config.group_context_examples:
        warnings.append("group_context_examples is empty (group small-talk may be weaker)")

    if not _has_bot_or_ai_cue(config):
        warnings.append(
            "no bot/AI-related substring in response_examples or group_context_examples "
            f"(expected one of: {', '.join(_BOT_OR_AI_MARKERS[:4])}…)"
        )

    return errors, warnings


def assert_persona_yaml_file_valid(yaml_path: str) -> None:
    """Load persona YAML via Pydantic, reject secrets, enforce semantic rules.

    Args:
        yaml_path: Path to ``persona.yaml``.

    Raises:
        ValueError: If forbidden patterns are found, load fails, or semantic errors exist.
    """
    path = Path(yaml_path)
    raw = path.read_text(encoding="utf-8")
    for pattern, label in _SECRET_PATTERNS:
        if pattern.search(raw):
            raise ValueError(f"{path}: remove {label} from repo; use env vars (see README)")
    config = load_persona(yaml_path)
    errs, warns = collect_persona_semantic_issues(config)
    for w in warns:
        print(f"WARNING {path}: {w}", file=sys.stderr)
    if errs:
        raise ValueError(f"{path}: " + "; ".join(errs))


def validate_all_personas_under(personas_dir: str) -> None:
    """Validate every ``*/persona.yaml`` under a directory.

    Args:
        personas_dir: Root folder that contains persona subdirectories.

    Raises:
        FileNotFoundError: If ``personas_dir`` is missing.
        ValueError: On forbidden secret patterns, invalid persona data, or semantic errors.
    """
    root = Path(personas_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"Not a directory: {personas_dir}")
    for yaml_file in sorted(root.glob("*/persona.yaml")):
        assert_persona_yaml_file_valid(str(yaml_file))
