"""Tests for entity extraction and response examples."""
import tempfile
import os

from src.memory.user_memory import (
    UserMemoryStore,
    _extract_dog_info,
    _extract_fitness_info,
    _extract_generic_info,
)
from src.core.persona_manager import load_persona


class TestDogEntityExtractor:
    """Test dog-specific entity extraction."""
    
    def test_extract_breed(self):
        data = {"dog_breed": None, "dog_problems": []}
        _extract_dog_info(data, "У меня лабрадор, 3 года")
        assert data["dog_breed"] == "Лабрадор"
    
    def test_extract_multiple_breeds(self):
        """Should extract first breed only."""
        data = {"dog_breed": None, "dog_problems": []}
        _extract_dog_info(data, "Был овчарка, теперь хаски")
        assert data["dog_breed"] == "Немецкая овчарка"
    
    def test_extract_problem(self):
        data = {"dog_breed": None, "dog_problems": []}
        _extract_dog_info(data, "У пса аллергия на курицу")
        assert "аллергия" in data["dog_problems"]
    
    def test_extract_age(self):
        data = {"dog_breed": None, "dog_problems": []}
        _extract_dog_info(data, "Ей 5 лет")
        assert data["dog_age"] == "5 лет"
    
    def test_extract_name(self):
        data = {"dog_breed": None, "dog_problems": []}
        _extract_dog_info(data, "Его зовут Рекс")
        assert data["dog_name"] == "Рекс"
    
    def test_doesnt_overwrite_existing(self):
        data = {"dog_breed": "Хаски", "dog_problems": []}
        _extract_dog_info(data, "У меня лабрадор")
        assert data["dog_breed"] == "Хаски"  # not overwritten


class TestFitnessEntityExtractor:
    """Test fitness-specific entity extraction."""
    
    def test_extract_goal(self):
        data = {"interests": [], "health_issues": []}
        _extract_fitness_info(data, "Хочу похудеть к лету")
        assert "снижение веса" in data["interests"]
    
    def test_extract_health_issue(self):
        data = {"interests": [], "health_issues": []}
        _extract_fitness_info(data, "Болит колено после бега")
        assert "проблемы с коленями" in data["health_issues"]


class TestGenericEntityExtractor:
    """Test generic entity extraction."""
    
    def test_extract_topic(self):
        data = {"interests": []}
        _extract_generic_info(data, "Расскажу про здоровье")
        assert "здоровье" in data["interests"]


class TestExtractorRegistry:
    """Test persona-to-extractor mapping."""
    
    def test_kormoved_uses_dog_extractor(self):
        with tempfile.TemporaryDirectory() as d:
            store = UserMemoryStore(memory_dir=d, persona_name="kormoved", entity_profile="dog")
            assert store._extractor == _extract_dog_info
    
    def test_fitness_uses_fitness_extractor(self):
        with tempfile.TemporaryDirectory() as d:
            store = UserMemoryStore(memory_dir=d, persona_name="fitness", entity_profile="fitness")
            assert store._extractor == _extract_fitness_info
    
    def test_unknown_uses_generic(self):
        with tempfile.TemporaryDirectory() as d:
            store = UserMemoryStore(memory_dir=d, persona_name="unknown_persona")
            assert store._extractor == _extract_generic_info
    
    def test_dog_profile_maps_to_dog(self):
        with tempfile.TemporaryDirectory() as d:
            store = UserMemoryStore(memory_dir=d, persona_name="any_slug", entity_profile="dog")
            assert store._extractor == _extract_dog_info


class TestFitnessMemoryStore:
    """Test memory store with fitness persona extracts fitness data."""
    
    def test_fitness_interests_extracted(self, tmp_path):
        mem_dir = str(tmp_path / "memory")
        store = UserMemoryStore(memory_dir=mem_dir, persona_name="fitness", entity_profile="fitness")
        
        store.record_group_message("456", "user1", "User1", "789", "Chat", "Хочу набрать массу")
        
        data = store._load("456")
        assert "набор массы" in data.get("interests", [])


class TestResponseExamplesInPersona:
    """Test response_examples field in persona YAML."""
    
    def test_response_examples_loaded(self):
        persona_yaml = os.path.join(
            os.path.dirname(__file__), "..", "personas", "kormoved", "persona.yaml"
        )
        if os.path.exists(persona_yaml):
            persona = load_persona(persona_yaml)
            assert len(persona.response_examples) > 0
            
            first = persona.response_examples[0]
            assert first.trigger != ""
            assert first.bad_response != ""
            assert first.good_response != ""
    
    def test_response_examples_different_from_bad(self):
        """Good response should be meaningfully different from bad."""
        persona_yaml = os.path.join(
            os.path.dirname(__file__), "..", "personas", "kormoved", "persona.yaml"
        )
        if os.path.exists(persona_yaml):
            persona = load_persona(persona_yaml)
            for ex in persona.response_examples:
                # Good should not contain "к сожалению" or "рекомендуем"
                assert "к сожалению" not in ex.good_response.lower()
                assert "рекомендуем" not in ex.good_response.lower()


class TestResponseExamplesFormatting:
    """Test generator formats response examples for prompts."""
    
    def test_get_response_examples_text_empty(self):
        from src.responders.generator import ResponseGenerator
        from src.utils.llm_client import LLMClient
        
        gen = ResponseGenerator(
            llm_client=LLMClient(api_key="fake"),
            model="fake",
            contract={},
            response_examples=[],
        )
        assert gen._get_response_examples_text() == "(примеров нет)"
    
    def test_get_response_examples_text_with_data(self):
        from src.responders.generator import ResponseGenerator
        from src.utils.llm_client import LLMClient
        
        gen = ResponseGenerator(
            llm_client=LLMClient(api_key="fake"),
            model="fake",
            contract={},
            response_examples=[
                {"trigger": "Дорого", "bad": "Наши цены...", "good": "Посчитай..."}
            ],
        )
        text = gen._get_response_examples_text()
        assert "Дорого" in text
        assert "Посчитай" in text
        assert "❌" in text
        assert "✅" in text
