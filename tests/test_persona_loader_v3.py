"""Tests for Persona Loader v3 — Pydantic-based loading."""
import pytest
import os
import tempfile
from src.core.persona_loader_v3 import PersonaLoaderV3, PersonaConfigV3


YAML_CONTENT = """
persona:
  name: "TestBot"
  platform: "telegram"
  account_type: "userbot"
  session_name: "test"
  personality: "Тестовый бот"
  
  vibe:
    role: "Тестер"
    voice: "Простой"
    core_emotions: ["fun"]
    values: ["Тестирование"]
    taboos: ["политика"]
  
  behavior:
    on_greeting: "Привет!"
    on_tool_success: "Отлично"
    on_dm: "Болтай"
    on_taboo: "Не моя тема"
    always: "Ты живой"
    greeting_policy:
      enabled: true
      greet_only_first_response: true
      greeting_variants:
        - "Привет!"
    validators:
      banned_phrases:
        - "я бот"
    context_policy:
      namespace: "test_ns"
      keep_keys: ["name"]
      ttl_turns: 5
  
  product:
    name: "Тестовый продукт"
    price: "100₽"
  
  triggers:
    respond_when:
      - keywords: ["тест", "проверка"]
        topics: ["testing"]
        probability: 0.8
    ignore_when:
      - contains: ["спам"]
  
  conversation_flow:
    group_mode:
      max_messages_per_hour: 5
      probability_to_respond: 0.5
    dm_mode:
      greeting: "Привет!"
      funnel:
        - step: "спросить"
          trigger: "Что надо?"
  
  anti_spam:
    min_delay_between_messages: 10
    max_delay_between_messages: 60
    leave_on_read: 0.5
    emoji_reaction: 0.2
  
  memory:
    remember: ["имя", "тема"]
    reference_past: true
  
  response_examples:
    - trigger: "Тест?"
      bad_response: "Плохо"
      good_response: "Хорошо"
  
  competitor_knowledge: "Конкурент1: норм"
  router_model: "openrouter/test"
  generator_model: "openrouter/test-gen"
"""


@pytest.fixture
def yaml_file(tmp_path):
    path = tmp_path / "persona.yaml"
    path.write_text(YAML_CONTENT, encoding="utf-8")
    return str(path)


@pytest.fixture
def config(yaml_file):
    return PersonaLoaderV3.load(yaml_file)


class TestPersonaLoaderV3:
    def test_load_name(self, config):
        assert config.name == "TestBot"
    
    def test_load_platform(self, config):
        assert config.platform == "telegram"
        assert config.account_type == "userbot"
    
    def test_load_vibe(self, config):
        assert config.vibe is not None
        assert config.vibe.role == "Тестер"
        assert "fun" in config.vibe.core_emotions
        assert "политика" in config.vibe.taboos
    
    def test_load_behavior(self, config):
        assert config.behavior is not None
        assert config.behavior.on_greeting == "Привет!"
        assert config.behavior.always == "Ты живой"
        assert config.behavior.on_dm == "Болтай"
        assert config.behavior.on_taboo == "Не моя тема"
    
    def test_load_behavior_nested(self, config):
        assert config.behavior.greeting_policy is not None
        assert config.behavior.greeting_policy.enabled is True
        assert "Привет!" in config.behavior.greeting_policy.greeting_variants
        
        assert config.behavior.validators is not None
        assert "я бот" in config.behavior.validators.banned_phrases
        
        assert config.behavior.context_policy is not None
        assert config.behavior.context_policy.namespace == "test_ns"
        assert config.behavior.context_policy.ttl_turns == 5
    
    def test_load_triggers(self, config):
        assert len(config.respond_triggers) == 1
        assert "тест" in config.respond_triggers[0].keywords
        assert len(config.ignore_triggers) == 1
        assert "спам" in config.ignore_triggers[0].contains
    
    def test_load_anti_spam(self, config):
        assert config.anti_spam.leave_on_read == 0.5
        assert config.anti_spam.min_delay_between_messages == 10
    
    def test_load_memory(self, config):
        assert "имя" in config.memory.remember
        assert config.memory.reference_past is True
    
    def test_load_response_examples(self, config):
        assert len(config.response_examples) == 1
        assert config.response_examples[0].trigger == "Тест?"
    
    def test_load_competitor_knowledge(self, config):
        assert "Конкурент1" in config.competitor_knowledge
    
    def test_load_product(self, config):
        assert config.product.name == "Тестовый продукт"
        assert config.product.price == "100₽"
    
    def test_load_conversation_flow(self, config):
        assert config.group_mode.max_messages_per_hour == 5
        assert config.dm_mode.greeting == "Привет!"
        assert len(config.dm_mode.funnel) == 1
    
    def test_load_models(self, config):
        assert "test" in config.router_model
    
    def test_load_yaml_path(self, config, yaml_file):
        assert config.yaml_path == yaml_file


class TestPersonaLoaderV3Errors:
    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            PersonaLoaderV3.load("/nonexistent/file.yaml")
    
    def test_invalid_name(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text("persona:\n  name: ''\n", encoding="utf-8")
        with pytest.raises(ValueError):
            PersonaLoaderV3.load(str(path))


class TestPersonaLoaderV3Discover:
    def test_discover_empty(self, tmp_path):
        personas = PersonaLoaderV3.discover(str(tmp_path))
        assert personas == []
    
    def test_discover_one(self, tmp_path):
        subdir = tmp_path / "test"
        subdir.mkdir()
        (subdir / "persona.yaml").write_text(YAML_CONTENT, encoding="utf-8")
        
        personas = PersonaLoaderV3.discover(str(tmp_path))
        assert len(personas) == 1
        assert personas[0].name == "TestBot"
    
    def test_discover_skips_invalid(self, tmp_path):
        subdir1 = tmp_path / "good"
        subdir1.mkdir()
        (subdir1 / "persona.yaml").write_text(YAML_CONTENT, encoding="utf-8")
        
        subdir2 = tmp_path / "bad"
        subdir2.mkdir()
        (subdir2 / "persona.yaml").write_text("not: valid: yaml: [[[[", encoding="utf-8")
        
        personas = PersonaLoaderV3.discover(str(tmp_path))
        assert len(personas) == 1  # Only the good one
