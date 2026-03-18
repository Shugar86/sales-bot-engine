"""Tests for Turing Test edge cases — pre-filters, go-away, bot denial, fallbacks."""
import pytest
import json
from unittest.mock import AsyncMock, MagicMock

from src.core.router import (
    MessageRouter, Decision, RouteResult,
    GO_AWAY_PATTERNS, BOT_QUESTION_PATTERNS, SPAM_PATTERNS,
)
from src.responders.generator import (
    ResponseGenerator, GeneratedResponse,
    GO_AWAY_PATTERNS as GEN_GO_AWAY,
    BOT_QUESTION_PATTERNS as GEN_BOT_Q,
    FALLBACK_GROUP, FALLBACK_DM,
)


@pytest.fixture
def mock_llm():
    client = MagicMock()
    client.call = AsyncMock()
    return client


@pytest.fixture
def sample_contract():
    return {
        "persona": {
            "name": "Андрей",
            "backstory": "Кинолог-консультант, 12 лет опыта",
            "speaking_style": {
                "tone": "бывалый",
                "patterns": ["Коротко"],
                "forbidden": ["Спам"],
            },
        },
        "product": {
            "products": [{"name": "Корм Проф", "description": "Для собак"}]
        },
        "triggers": {
            "respond_to": [{"context": "Кормление", "keywords": ["корм"]}],
            "ignore": ["Политика"],
        },
        "conversation_flow": {
            "group_chat": {"steps": ["Встрять"]},
            "never": ["Спамить"],
        }
    }


@pytest.fixture
def router(mock_llm, sample_contract):
    return MessageRouter(llm_client=mock_llm, model="test", contract=sample_contract)


@pytest.fixture
def generator(mock_llm, sample_contract):
    return ResponseGenerator(llm_client=mock_llm, model="test", contract=sample_contract)


class TestRouterPrefilters:
    """Тесты пред-фильтров роутера (без LLM)."""
    
    @pytest.mark.asyncio
    async def test_go_away_detected(self, router, mock_llm):
        """'Отстань' → DISENGAGE без вызова LLM."""
        for phrase in ["отстань", "хватит пишешь", "не пиши мне", "перестань"]:
            result = await router.route(phrase)
            assert result.decision == Decision.DISENGAGE, f"Failed for: {phrase}"
            assert result.confidence == 1.0
        
        # LLM не должен вызываться
        mock_llm.call.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_bot_question_detected(self, router, mock_llm):
        """'Ты бот?' → RESPOND без вызова LLM."""
        for phrase in ["ты бот?", "ты ai?", "ты нейросеть", "это автоматический ответ"]:
            result = await router.route(phrase)
            assert result.decision == Decision.RESPOND, f"Failed for: {phrase}"
            assert result.confidence == 1.0
        
        mock_llm.call.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_spam_links_ignored(self, router, mock_llm):
        """Спам-ссылки → IGNORE."""
        spam_messages = [
            "Подпишись на канал https://t.me/spam",
            "Бесплатный курс по крипте",
            "+79001234567 звони",
        ]
        for msg in spam_messages:
            result = await router.route(msg)
            assert result.decision == Decision.IGNORE, f"Failed for: {msg}"
    
    @pytest.mark.asyncio
    async def test_trivial_reactions_ignored(self, router, mock_llm):
        """Короткие реакции → IGNORE."""
        trivial = ["лол", "👍", "+1", "ок", "ага", "😂", "))))"]
        for msg in trivial:
            result = await router.route(msg)
            assert result.decision == Decision.IGNORE, f"Failed for: {msg}"
    
    @pytest.mark.asyncio
    async def test_empty_message_ignored(self, router, mock_llm):
        """Пустое → IGNORE."""
        result = await router.route("")
        assert result.decision == Decision.IGNORE
    
    @pytest.mark.asyncio
    async def test_bare_link_ignored(self, router, mock_llm):
        """Голая ссылка → IGNORE."""
        result = await router.route("https://example.com")
        assert result.decision == Decision.IGNORE
    
    @pytest.mark.asyncio
    async def test_meaningful_message_hits_llm(self, router, mock_llm):
        """Нормальное сообщение → идёт в LLM."""
        mock_llm.call.return_value = MagicMock(
            text=json.dumps({"decision": "RESPOND", "confidence": 0.9, "reason": "test"}),
            success=True,
        )
        
        result = await router.route("Какой корм лучше для овчарки?")
        
        # LLM должен быть вызван
        mock_llm.call.assert_called_once()


class TestGeneratorGoAway:
    """Тесты генератора: обработка 'отстань'."""
    
    def test_go_away_detection(self, generator):
        """Проверка паттернов 'отстань'."""
        for phrase in ["отстань", "хватит", "уйди", "не пиши мне"]:
            assert generator.detect_go_away(phrase), f"Failed for: {phrase}"
    
    def test_go_away_response_exists(self, generator):
        """Ответ на 'отстань' не пустой и без AI-маркеров."""
        response = generator.generate_go_away_response("Андрей")
        
        assert response.text
        assert len(response.text) > 10
        assert response.tone == "casual"
        assert response.stage == "disengage"
        # Не содержит AI-маркеров
        text_lower = response.text.lower()
        assert "ai" not in text_lower
        assert "бот" not in text_lower
        assert "нейросет" not in text_lower
        assert "извините" not in text_lower


class TestGeneratorBotDenial:
    """Тесты генератора: 'ты бот?' — ответ как человек."""
    
    def test_bot_question_detection(self, generator):
        for phrase in ["ты бот?", "ты ai?", "ты нейросеть"]:
            assert generator.detect_bot_question(phrase), f"Failed for: {phrase}"
    
    def test_bot_denial_response(self, generator):
        response = generator.generate_bot_denial_response("Андрей")
        
        assert response.text
        assert response.tone == "humor"
        assert response.stage == "engage"
        # Ответ должен быть уверенным, как живой человек
        text_lower = response.text.lower()
        assert "нет" in text_lower or "серьёзно" in text_lower or "ахах" in text_lower


class TestGeneratorFallback:
    """Тесты fallback при ошибках LLM."""
    
    @pytest.mark.asyncio
    async def test_group_llm_failure_fallback(self, generator, mock_llm):
        """Ошибка LLM в группе → fallback ответ (не молчание)."""
        mock_llm.call.return_value = MagicMock(text="", success=False, error="timeout")
        
        result = await generator.generate_group_response("Тестовое сообщение")
        
        assert result is not None
        assert result.text
        assert len(result.text) > 5
        # Fallback должен быть на русском
        assert any(c in result.text for c in "абвгдежзиклмнопрстуфхцчшщэюя")
    
    @pytest.mark.asyncio
    async def test_dm_llm_failure_fallback(self, generator, mock_llm):
        """Ошибка LLM в ЛС → fallback ответ (не молчание)."""
        mock_llm.call.return_value = MagicMock(text="", success=False, error="timeout")
        
        result = await generator.generate_dm_response("Хочу купить корм")
        
        assert result is not None
        assert result.text
    
    def test_parse_non_json_text(self, generator):
        """LLM вернул текст без JSON — парсим как есть."""
        result = generator._parse_response("Привет! Как дела? Это обычный текст.")
        
        assert result is not None
        assert "Привет" in result.text
    
    def test_parse_empty_returns_none(self, generator):
        """Пустой текст → None."""
        result = generator._parse_response("")
        assert result is None


class TestTuringTestScenarios:
    """Сценарии Теста Тьюринга — Baldy mode."""
    
    @pytest.mark.asyncio
    async def test_competitor_price_question(self, router, mock_llm):
        """Вопрос про цену конкурента → RESPOND (не игнорируем)."""
        mock_llm.call.return_value = MagicMock(
            text=json.dumps({
                "decision": "RESPOND",
                "confidence": 0.8,
                "reason": "Вопрос про корм — моя тема",
                "keywords": ["цена", "корм"],
            }),
            success=True,
        )
        
        result = await router.route("А сколько стоит корм у Петровича?")
        assert result.decision == Decision.RESPOND
    
    @pytest.mark.asyncio
    async def test_adjacent_topic_engage(self, router, mock_llm):
        """Смежная тема → ENGAGE."""
        mock_llm.call.return_value = MagicMock(
            text=json.dumps({
                "decision": "ENGAGE",
                "confidence": 0.6,
                "reason": "Смежная тема про животных",
                "keywords": ["кошка"],
            }),
            success=True,
        )
        
        result = await router.route("У меня кошка не ест, что делать?")
        assert result.decision in [Decision.ENGAGE, Decision.RESPOND]
    
    @pytest.mark.asyncio
    async def test_politics_ignored(self, router, mock_llm):
        """Политика → IGNORE."""
        mock_llm.call.return_value = MagicMock(
            text=json.dumps({
                "decision": "IGNORE",
                "confidence": 0.95,
                "reason": "Политика, не по теме",
            }),
            success=True,
        )
        
        result = await router.route("А что думаешь про выборы?")
        assert result.decision == Decision.IGNORE
    
    @pytest.mark.asyncio
    async def test_already_answered_ignored(self, router, mock_llm):
        """Кто-то уже ответил → IGNORE."""
        mock_llm.call.return_value = MagicMock(
            text=json.dumps({
                "decision": "IGNORE",
                "confidence": 0.9,
                "reason": "Уже ответили",
            }),
            success=True,
        )
        
        context = "Пользователь1: Попробуйте Роял Канин\nПользователь2: Согласен"
        result = await router.route("Какой корм для овчарки?", chat_context=context)
        assert result.decision == Decision.IGNORE
