"""Tests for PersonaManager — v2 persona loading and discovery."""
import pytest
import yaml

from src.core.persona_manager import (
    load_persona,
    discover_personas,
    PersonaManager,
)


@pytest.fixture
def valid_persona_yaml(tmp_path):
    """Create a valid persona YAML file."""
    persona_dir = tmp_path / "test_persona"
    persona_dir.mkdir()
    
    data = {
        "persona": {
            "name": "TestPersona",
            "platform": "telegram",
            "account_type": "userbot",
            "session_name": "test",
            "api_id": 12345,
            "api_hash": "abc123",
            "phone": "+79001234567",
            "personality": "Ты тестовый персонаж",
            "groups_to_monitor": ["-100123", "-100456"],
            "product": {
                "name": "Test Product",
                "price": "1000₽",
                "link": "https://test.com",
                "description": "A test product",
            },
            "triggers": {
                "respond_when": [
                    {
                        "keywords": ["тест", "проверка"],
                        "topics": ["testing"],
                    }
                ],
                "ignore_when": [
                    {
                        "contains": ["спам"],
                        "from_bot": True,
                    }
                ],
            },
            "conversation_flow": {
                "group_mode": {
                    "max_messages_per_hour": 5,
                    "style": "дружелюбный",
                },
                "dm_mode": {
                    "greeting": "Привет!",
                    "funnel": [
                        {"step": "помочь"},
                        {"step": "продать"},
                    ],
                },
            },
            "anti_spam": {
                "min_delay_between_messages": 60,
                "max_delay_between_messages": 300,
                "typing_simulation": True,
                "random_typos": False,
            },
            "router_model": "openrouter/test-fast",
            "generator_model": "openrouter/test-slow",
        }
    }
    
    yaml_path = persona_dir / "persona.yaml"
    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True)
    
    return str(yaml_path)


@pytest.fixture
def personas_dir(tmp_path):
    """Create a directory with multiple persona subdirectories."""
    # Persona 1
    p1_dir = tmp_path / "persona1"
    p1_dir.mkdir()
    with open(p1_dir / "persona.yaml", "w") as f:
        yaml.dump({
            "persona": {
                "name": "Alpha",
                "platform": "telegram",
                "account_type": "userbot",
                "personality": "Alpha persona",
            }
        }, f)
    
    # Persona 2
    p2_dir = tmp_path / "persona2"
    p2_dir.mkdir()
    with open(p2_dir / "persona.yaml", "w") as f:
        yaml.dump({
            "persona": {
                "name": "Beta",
                "platform": "vk",
                "account_type": "userbot",
                "personality": "Beta persona",
            }
        }, f)
    
    # Non-persona directory (no YAML)
    junk_dir = tmp_path / "junk"
    junk_dir.mkdir()
    (junk_dir / "readme.txt").write_text("not a persona")
    
    return str(tmp_path)


class TestLoadPersona:
    """Test loading a single persona from YAML."""
    
    def test_load_valid_persona(self, valid_persona_yaml):
        persona = load_persona(valid_persona_yaml)
        
        assert persona.name == "TestPersona"
        assert persona.platform == "telegram"
        assert persona.account_type == "userbot"
        assert persona.phone == "+79001234567"
        assert persona.api_id == 12345
        assert persona.api_hash == "abc123"
        assert persona.session_name == "test"
        assert persona.personality == "Ты тестовый персонаж"
    
    def test_load_triggers(self, valid_persona_yaml):
        persona = load_persona(valid_persona_yaml)
        
        assert len(persona.respond_triggers) == 1
        assert "тест" in persona.respond_triggers[0].keywords

        assert len(persona.ignore_triggers) == 1
        assert "спам" in persona.ignore_triggers[0].contains
    
    def test_load_conversation_flow(self, valid_persona_yaml):
        persona = load_persona(valid_persona_yaml)
        
        assert persona.group_mode.max_messages_per_hour == 5
        assert persona.dm_mode.greeting == "Привет!"
        assert len(persona.dm_mode.funnel) == 2
    
    def test_load_anti_spam(self, valid_persona_yaml):
        persona = load_persona(valid_persona_yaml)
        
        assert persona.anti_spam.min_delay_between_messages == 60
        assert persona.anti_spam.max_delay_between_messages == 300
        assert persona.anti_spam.typing_simulation is True
        assert persona.anti_spam.random_typos is False
    
    def test_load_product(self, valid_persona_yaml):
        persona = load_persona(valid_persona_yaml)
        
        assert persona.product_name == "Test Product"
        assert persona.product_price == "1000₽"
        assert persona.product_link == "https://test.com"
    
    def test_load_models(self, valid_persona_yaml):
        persona = load_persona(valid_persona_yaml)
        
        assert persona.router_model == "openrouter/test-fast"
        assert persona.generator_model == "openrouter/test-slow"
    
    def test_load_groups(self, valid_persona_yaml):
        persona = load_persona(valid_persona_yaml)
        
        assert "-100123" in persona.groups_to_monitor
        assert "-100456" in persona.groups_to_monitor
    
    def test_yaml_path_stored(self, valid_persona_yaml):
        persona = load_persona(valid_persona_yaml)
        assert persona.yaml_path == valid_persona_yaml
    
    def test_load_minimal_persona(self, tmp_path):
        """Minimal persona with only name should load with defaults."""
        yaml_path = tmp_path / "minimal.yaml"
        with open(yaml_path, "w") as f:
            yaml.dump({"persona": {"name": "Minimal"}}, f)
        
        persona = load_persona(str(yaml_path))
        
        assert persona.name == "Minimal"
        assert persona.platform == "telegram"  # default
        assert persona.account_type == "userbot"  # default
        assert persona.anti_spam.min_delay_between_messages == 30  # default
    
    def test_load_nonexistent_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_persona(str(tmp_path / "nonexistent.yaml"))

    def test_env_overrides_phone(self, valid_persona_yaml, monkeypatch):
        """session_name *test* → TEST_PHONE overlays YAML phone."""
        monkeypatch.setenv("TEST_PHONE", "+79990001122")
        persona = load_persona(valid_persona_yaml)
        assert persona.phone == "+79990001122"


class TestDiscoverPersonas:
    """Test persona directory discovery."""
    
    def test_discovers_all_personas(self, personas_dir):
        personas = discover_personas(personas_dir)
        
        assert len(personas) == 2
        names = {p.name for p in personas}
        assert "Alpha" in names
        assert "Beta" in names
    
    def test_skips_non_persona_dirs(self, personas_dir):
        personas = discover_personas(personas_dir)
        
        # Junk dir should be skipped (no persona.yaml)
        for p in personas:
            assert p.name != "junk"
    
    def test_empty_directory(self, tmp_path):
        personas = discover_personas(str(tmp_path))
        assert personas == []
    
    def test_nonexistent_directory(self, tmp_path):
        personas = discover_personas(str(tmp_path / "does_not_exist"))
        assert personas == []
    
    def test_handles_broken_yaml_gracefully(self, tmp_path):
        """Broken YAML should be skipped, not crash."""
        p_dir = tmp_path / "broken"
        p_dir.mkdir()
        with open(p_dir / "persona.yaml", "w") as f:
            f.write("{broken: yaml: [[[}")
        
        personas = discover_personas(str(tmp_path))
        # Should not crash, just skip the broken one
        assert len(personas) == 0


class TestPersonaManager:
    """Test PersonaManager class."""
    
    def test_load_all(self, personas_dir):
        manager = PersonaManager(personas_dir=personas_dir)
        personas = manager.load_all()
        
        assert len(personas) == 2
        assert len(manager.personas) == 2
    
    def test_get_persona_by_name(self, personas_dir):
        manager = PersonaManager(personas_dir=personas_dir)
        manager.load_all()
        
        alpha = manager.get_persona("Alpha")
        assert alpha is not None
        assert alpha.name == "Alpha"
    
    def test_get_persona_not_found(self, personas_dir):
        manager = PersonaManager(personas_dir=personas_dir)
        manager.load_all()
        
        result = manager.get_persona("NonExistent")
        assert result is None
