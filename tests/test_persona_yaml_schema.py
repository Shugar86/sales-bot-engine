"""Validate all shipped persona YAML files (schema + hygiene)."""

from pathlib import Path

import pytest

from src.core.persona_yaml_validate import (
    assert_persona_yaml_file_valid,
    validate_all_personas_under,
)


def test_all_repo_personas_validate() -> None:
    """Every personas/*/persona.yaml must load and pass secret-pattern checks."""
    repo_root = Path(__file__).resolve().parents[1]
    validate_all_personas_under(str(repo_root / "personas"))


def test_validate_all_rejects_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        validate_all_personas_under(str(tmp_path / "nope"))


def test_assert_persona_rejects_phone_in_content(tmp_path: Path) -> None:
    bad = tmp_path / "bad" / "persona.yaml"
    bad.parent.mkdir()
    bad.write_text(
        "persona:\n  name: X\n  phone: '+79001234567'\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="phone"):
        assert_persona_yaml_file_valid(str(bad))
