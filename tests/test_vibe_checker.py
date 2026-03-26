"""Tests for Vibe Checker — v3 core module."""
import pytest
from src.core.vibe_checker import VibeChecker
from src.core.context_reader import ChatContext, Message, VibeType


# Persona config fixtures
KORMOVED_CONFIG = {
    "vibe": {
        "role": "Консультант по кормлению собак",
        "taboos": ["политика", "религия"],
    },
    "triggers": {
        "respond_when": [
            {"keywords": ["корм", "кормить", "ест", "аллергия", "понос"],
             "topics": ["кормление собак", "проблемы со здоровьем"]},
        ],
        "ignore_when": [
            {"contains": ["спам", "реклама"]},
        ],
    },
    "behavior": {
        "always": "Делись личным опытом, болтай как человек",
    },
}

FITNESS_CONFIG = {
    "vibe": {
        "role": "Фитнес-консультант",
        "taboos": ["политика"],
    },
    "triggers": {
        "respond_when": [
            {"keywords": ["тренировка", "мышцы", "похудеть", "зал", "белок"],
             "topics": ["тренировки", "питание"]},
        ],
        "ignore_when": [
            {"contains": ["спам"]},
        ],
    },
    "behavior": {
        "always": "Делись опытом из зала",
    },
}


@pytest.fixture
def kormoved_checker():
    return VibeChecker(KORMOVED_CONFIG)


@pytest.fixture
def fitness_checker():
    return VibeChecker(FITNESS_CONFIG)


@pytest.fixture
def empty_context():
    return ChatContext()


@pytest.fixture
def casual_context():
    return ChatContext(
        messages=[
            Message(1, "u1", "a", "A", "Привет", 100),
            Message(2, "u2", "b", "B", "Привет!", 200),
        ],
        vibe=VibeType.CASUAL.value,
    )


@pytest.fixture
def directed_context():
    return ChatContext(
        messages=[
            Message(1, "bot", "bot", "Bot", "Ответ", 100),
            Message(2, "u1", "a", "A", "А ты что думаешь?", 200, is_reply_to_me=True),
        ],
        is_directed_at_me=True,
        vibe=VibeType.CASUAL.value,
    )


class TestVibeCheckerBasic:
    """Basic vibe checker tests."""
    
    def test_empty_message(self, kormoved_checker, empty_context):
        result = kormoved_checker.check(empty_context, "")
        assert result.should_respond is False
        assert "Пустое" in result.reason
    
    def test_keyword_match_food(self, kormoved_checker, empty_context):
        result = kormoved_checker.check(
            empty_context,
            "Собака не ест корм уже 3 дня"
        )
        assert result.should_respond is True
        assert result.match_type == "keyword"
    
    def test_keyword_match_health(self, kormoved_checker, empty_context):
        result = kormoved_checker.check(
            empty_context,
            "У собаки понос и аллергия"
        )
        assert result.should_respond is True
    
    def test_no_keyword_match(self, kormoved_checker, empty_context):
        result = kormoved_checker.check(
            empty_context,
            "Какой фильм посмотреть?"
        )
        # Might match on personal opportunity or not
        assert result.match_type in ["personal", "humor", "none"]
    
    def test_ignore_pattern(self, kormoved_checker, empty_context):
        result = kormoved_checker.check(
            empty_context,
            "Купи спам у нас дёшево!"
        )
        assert result.should_respond is False
        assert "Игнорируемый" in result.reason
    
    def test_directed_at_me_responds(self, kormoved_checker, directed_context):
        result = kormoved_checker.check(
            directed_context,
            "А ты что думаешь?"
        )
        assert result.should_respond is True
        assert result.match_type == "mention"


class TestVibeCheckerTaboo:
    """Tests for taboo topic handling."""
    
    def test_politics_taboo(self, kormoved_checker, empty_context):
        result = kormoved_checker.check(
            empty_context,
            "Что думаешь про политику и выборы?"
        )
        assert result.should_respond is True
        assert result.match_type == "taboo"
        assert "taboo_deflect" in result.suggested_angle
    
    def test_religion_taboo(self, kormoved_checker, empty_context):
        result = kormoved_checker.check(
            empty_context,
            "А ты веришь в бога?"
        )
        assert result.should_respond is True
        assert result.match_type == "taboo"
    
    def test_no_taboo(self, kormoved_checker, empty_context):
        result = kormoved_checker.check(
            empty_context,
            "Какую породу собаки завести?"
        )
        assert result.match_type != "taboo"


class TestVibeCheckerHumor:
    """Tests for humor opportunity detection."""
    
    def test_joke_request(self, kormoved_checker, empty_context):
        result = kormoved_checker.check(
            empty_context,
            "Расскажи анекдот или шутку"
        )
        assert result.should_respond is True
        assert result.match_type == "humor"
    
    def test_funny_vibe_chat(self, kormoved_checker):
        context = ChatContext(
            messages=[
                Message(1, "u1", "a", "A", "😂😂", 100),
            ],
            vibe=VibeType.FUNNY.value,
        )
        result = kormoved_checker.check(context, "😂😂")
        # Should respond due to humor
        assert result.should_respond is True


class TestVibeCheckerPersonal:
    """Tests for personal opportunity detection."""
    
    def test_life_topic_with_question(self, kormoved_checker, casual_context):
        result = kormoved_checker.check(
            casual_context,
            "Что делать с собакой если она чешется?"
        )
        # Should match on keyword "собак" + question
        assert result.should_respond is True
    
    def test_general_chat_no_match(self, kormoved_checker):
        context = ChatContext(
            messages=[
                Message(1, "u1", "a", "A", "Погода норм", 100),
            ],
            vibe=VibeType.CASUAL.value,
        )
        result = kormoved_checker.check(context, "Погода норм сегодня")
        # No strong match
        assert result.confidence < 0.8


class TestVibeCheckerDifferentPersonas:
    """Tests with different persona configs."""
    
    def test_fitness_keyword_match(self, fitness_checker, empty_context):
        result = fitness_checker.check(
            empty_context,
            "Какие тренировки для похудеть и набрать мышцы?"
        )
        assert result.should_respond is True
        assert result.match_type == "keyword"
    
    def test_fitness_no_match_food(self, fitness_checker, empty_context):
        result = fitness_checker.check(
            empty_context,
            "Собака не ест корм"
        )
        assert result.should_respond is False
    
    def test_fitness_ignore_pattern(self, fitness_checker, empty_context):
        result = fitness_checker.check(
            empty_context,
            "Это спам реклама"
        )
        assert result.should_respond is False


class TestVibeCheckerConfidence:
    """Tests for confidence scoring."""
    
    def test_high_confidence_on_direct_mention(self, kormoved_checker, directed_context):
        result = kormoved_checker.check(directed_context, "А ты?")
        assert result.confidence >= 0.95
    
    def test_medium_confidence_on_keyword(self, kormoved_checker, empty_context):
        result = kormoved_checker.check(empty_context, "Корм для собаки")
        assert 0.5 <= result.confidence <= 0.95
    
    def test_low_confidence_no_match(self, kormoved_checker, empty_context):
        result = kormoved_checker.check(empty_context, "Просто текст без совпадений")
        assert result.confidence < 0.8


class TestVibeCheckerEdgeCases:
    """Edge case tests."""
    
    def test_very_long_message(self, kormoved_checker, empty_context):
        text = "корм " * 500
        result = kormoved_checker.check(empty_context, text)
        assert result.should_respond is True
    
    def test_unicode_emoji(self, kormoved_checker, empty_context):
        result = kormoved_checker.check(empty_context, "🐕‍🦺 корм для собаки")
        assert result.should_respond is True
    
    def test_mixed_case(self, kormoved_checker, empty_context):
        result = kormoved_checker.check(empty_context, "КОРМ для Собаки")
        assert result.should_respond is True
    
    def test_empty_context_messages(self, kormoved_checker):
        ctx = ChatContext(messages=[], vibe="casual")
        result = kormoved_checker.check(ctx, "Корм")
        assert result.should_respond is True
    
    def test_special_characters(self, kormoved_checker, empty_context):
        result = kormoved_checker.check(empty_context, "!!!корм!!!")
        assert result.should_respond is True
