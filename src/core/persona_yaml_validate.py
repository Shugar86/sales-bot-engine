"""Static checks for persona YAML files before runtime."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

from .persona_manager import load_persona

# (regex, human-readable label for error messages)
_SECRET_PATTERNS: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"\+7\s*\d{3}\s*\d{3}\s*\d{2}\s*\d{2}"), "phone number (+7…)"),
    (re.compile(r"sk-or-v1-[A-Za-z0-9_-]{20,}"), "OpenRouter API key pattern"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}"), "JWT-like secret"),
]


def assert_persona_yaml_file_valid(yaml_path: str) -> None:
    """Load persona YAML via Pydantic and reject obvious secrets in raw text.

    Args:
        yaml_path: Path to ``persona.yaml``.

    Raises:
        ValueError: If forbidden patterns are found or load fails.
    """
    path = Path(yaml_path)
    raw = path.read_text(encoding="utf-8")
    for pattern, label in _SECRET_PATTERNS:
        if pattern.search(raw):
            raise ValueError(f"{path}: remove {label} from repo; use env vars (see README)")
    load_persona(yaml_path)


def validate_all_personas_under(personas_dir: str) -> None:
    """Validate every ``*/persona.yaml`` under a directory.

    Args:
        personas_dir: Root folder that contains persona subdirectories.

    Raises:
        FileNotFoundError: If ``personas_dir`` is missing.
        ValueError: On forbidden secret patterns or invalid persona data.
    """
    root = Path(personas_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"Not a directory: {personas_dir}")
    for yaml_file in sorted(root.glob("*/persona.yaml")):
        assert_persona_yaml_file_valid(str(yaml_file))
