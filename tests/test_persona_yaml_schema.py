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


def _semantic_persona_yaml(
    *,
    max_messages_per_hour: int = 5,
    triggers_inner: str = '    respond_when:\n      - keywords: ["hi"]',
    response_examples_yaml: str,
    group_context_yaml: str = "  group_context_examples: []",
    backstory_body: str = "a" * 55,
) -> str:
    """Minimal persona document that satisfies Pydantic load + semantic shape."""
    return f"""persona:
  name: SemTest
  platform: telegram
  account_type: userbot
  personality: "p"
  groups_to_monitor: []
  triggers:
{triggers_inner}
  conversation_flow:
    group_mode:
      max_messages_per_hour: {max_messages_per_hour}
    dm_mode:
      greeting: ""
  vibe:
    role: R
    backstory: >
      {backstory_body}
    taboos: ["tabu"]
{group_context_yaml}
  response_examples:
{response_examples_yaml}
"""


def test_semantic_error_few_response_examples(tmp_path: Path) -> None:
    ex = """    - trigger: "1"
      bad_response: "b"
      good_response: "про бота спросили"
    - trigger: "2"
      bad_response: "b"
      good_response: "c"
"""
    p = tmp_path / "x" / "persona.yaml"
    p.parent.mkdir()
    p.write_text(_semantic_persona_yaml(response_examples_yaml=ex), encoding="utf-8")
    with pytest.raises(ValueError, match="response_examples"):
        assert_persona_yaml_file_valid(str(p))


def test_semantic_error_empty_respond_when(tmp_path: Path) -> None:
    ex = """    - trigger: "1"
      bad_response: "b"
      good_response: "не бот же"
    - trigger: "2"
      bad_response: "b"
      good_response: "c"
    - trigger: "3"
      bad_response: "b"
      good_response: "d"
"""
    p = tmp_path / "x" / "persona.yaml"
    p.parent.mkdir()
    p.write_text(
        _semantic_persona_yaml(
            response_examples_yaml=ex,
            triggers_inner="    respond_when: []",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="respond_when"):
        assert_persona_yaml_file_valid(str(p))


def test_semantic_error_max_messages_per_hour(tmp_path: Path) -> None:
    ex = """    - trigger: "1"
      bad_response: "b"
      good_response: "gpt не я"
    - trigger: "2"
      bad_response: "b"
      good_response: "c"
    - trigger: "3"
      bad_response: "b"
      good_response: "d"
"""
    p = tmp_path / "x" / "persona.yaml"
    p.parent.mkdir()
    p.write_text(
        _semantic_persona_yaml(
            response_examples_yaml=ex,
            max_messages_per_hour=11,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="max_messages_per_hour"):
        assert_persona_yaml_file_valid(str(p))


def test_semantic_warning_no_bot_cue(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    ex = """    - trigger: "1"
      bad_response: "b"
      good_response: "коротко"
    - trigger: "2"
      bad_response: "b"
      good_response: "ещё"
    - trigger: "3"
      bad_response: "b"
      good_response: "норм"
"""
    p = tmp_path / "x" / "persona.yaml"
    p.parent.mkdir()
    p.write_text(_semantic_persona_yaml(response_examples_yaml=ex), encoding="utf-8")
    assert_persona_yaml_file_valid(str(p))
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "bot/AI" in err or "бот" in err.lower()
