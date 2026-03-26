"""Tests for MessageRouter"""
import pytest
import json
from unittest.mock import AsyncMock, MagicMock
from src.core.router import MessageRouter, Decision, RouteResult


@pytest.fixture
def mock_llm():
    """Mock LLM клиент"""
    client = MagicMock()
    client.call = AsyncMock()
    return client


@pytest.fixture
def sample_contract():
    """Минимальный контракт для тестов"""
    return {
        "persona": {
            "name": "Андрей",
            "backstory": "Кинолог-консультант, 12 лет опыта",
        },
        "product": {
            "products": [
                {"name": "Корм Проф Спорт", "triggers": ["корм", "спорт"]},
                {"name": "Гипоаллергенный корм", "triggers": ["аллергия"]},
            ]
        },
        "triggers": {
            "respond_to": [
                {"context": "Кормление собак", "keywords": ["корм", "кормить"]},
                {"context": "Здоровье", "keywords": ["аллергия", "чешется"]},
            ],
            "ignore": ["Политика", "Не по теме"],
        },
        "conversation_flow": {
            "group_chat": {"steps": ["Встрять репликой", "Коротко"]},
            "never": ["Спамить", "Писать первым в ЛС"],
        }
    }


@pytest.fixture
def router(mock_llm, sample_contract):
    return MessageRouter(
        llm_client=mock_llm,
        model="google/gemini-2.0-flash-001",
        contract=sample_contract,
    )


class TestRouterDecisions:
    """Тесты решений роутера"""
    
    @pytest.mark.asyncio
    async def test_respond_to_korm_message(self, router, mock_llm):
        """Сообщение про корм → RESPOND"""
        mock_llm.call.return_value = MagicMock(
            text=json.dumps({
                "decision": "RESPOND",
                "confidence": 0.9,
                "reason": "Обсуждение кормления собаки",
                "topic": "кормление",
                "keywords": ["корм", "овчарка"]
            }),
            success=True,
        )
        
        result = await router.route("Подскажите хороший корм для овчарки")
        
        assert result.decision == Decision.RESPOND
        assert result.confidence >= 0.8
        assert "корм" in result.keywords_matched
    
    @pytest.mark.asyncio
    async def test_ignore_politics(self, router, mock_llm):
        """Сообщение про политику → IGNORE"""
        mock_llm.call.return_value = MagicMock(
            text=json.dumps({
                "decision": "IGNORE",
                "confidence": 0.95,
                "reason": "Не по теме собаководства",
            }),
            success=True,
        )
        
        result = await router.route("А что вы думаете про выборы?")
        
        assert result.decision == Decision.IGNORE
        assert result.confidence >= 0.9
    
    @pytest.mark.asyncio
    async def test_dm_always_sales(self, router, mock_llm):
        """ЛС → SALES_DM без LLM (после пред-фильтров)"""
        result = await router.route("Привет", is_dm=True)

        assert result.decision == Decision.SALES_DM
        assert result.confidence == 1.0
        mock_llm.call.assert_not_called()

    @pytest.mark.asyncio
    async def test_dm_go_away_uses_prefilter(self, router, mock_llm):
        """ЛС: «отстань» проходит через пред-фильтр → DISENGAGE, без LLM."""
        result = await router.route("отстань", is_dm=True)

        assert result.decision == Decision.DISENGAGE
        mock_llm.call.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_empty_message_ignored(self, router, mock_llm):
        """Пустое сообщение → IGNORE"""
        result = await router.route("")
        
        assert result.decision == Decision.IGNORE
        mock_llm.call.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_llm_error_safe_fallback(self, router, mock_llm):
        """Ошибка LLM → безопасный IGNORE"""
        mock_llm.call.return_value = MagicMock(
            text="",
            success=False,
            error="API timeout",
        )
        
        result = await router.route("Какой корм лучше?")
        
        assert result.decision == Decision.IGNORE
        assert "error" in result.reason.lower()
    
    @pytest.mark.asyncio
    async def test_malformed_json_fallback(self, router, mock_llm):
        """Кривой JSON → безопасный IGNORE"""
        mock_llm.call.return_value = MagicMock(
            text="Это не JSON вообще",
            success=True,
        )
        
        result = await router.route("Корм для собаки")
        
        assert result.decision == Decision.IGNORE


class TestRouterPromptBuilding:
    """Тесты сборки промпта"""
    
    def test_persona_summary_contains_name(self, router):
        summary = router._persona_summary
        assert "Андрей" in summary
    
    def test_persona_summary_contains_products(self, router):
        summary = router._persona_summary
        assert "Корм Проф Спорт" in summary
        assert "Гипоаллергенный корм" in summary
    
    def test_persona_summary_contains_triggers(self, router):
        summary = router._persona_summary
        assert "кормление" in summary.lower() or "корм" in summary.lower()


class TestRouterParseResponse:
    """Тесты парсинга ответа"""
    
    def test_parse_valid_json(self, router):
        text = '{"decision": "RESPOND", "confidence": 0.85, "reason": "test", "topic": "корм", "keywords": ["корм"]}'
        result = router._parse_response(text)
        
        assert result.decision == Decision.RESPOND
        assert result.confidence == 0.85
    
    def test_parse_markdown_block(self, router):
        text = '```json\n{"decision": "IGNORE", "confidence": 0.9, "reason": "spam"}\n```'
        result = router._parse_response(text)
        
        assert result.decision == Decision.IGNORE
    
    def test_parse_invalid_returns_ignore(self, router):
        result = router._parse_response("completely broken {[}")
        
        assert result.decision == Decision.IGNORE
        assert result.confidence == 0.0
