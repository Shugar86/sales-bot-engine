"""Tests for Orchestrator — multi-persona orchestrator."""
import pytest
import json
import yaml
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

from src.core.orchestrator import SalesBotOrchestrator, PersonaRuntime, BotState
from src.models.message import IncomingMessage, Platform
from src.core.persona_manager import PersonaConfig, TriggerConfig


@pytest.fixture
def personas_dir(tmp_path):
    """Create a minimal personas directory."""
    p_dir = tmp_path / "test_persona"
    p_dir.mkdir()
    
    data = {
        "persona": {
            "name": "TestBot",
            "platform": "telegram",
            "account_type": "userbot",
            "session_name": "test",
            "api_id": 12345,
            "api_hash": "testhash",
            "phone": "+79001234567",
            "personality": "Test bot for unit tests",
            "groups_to_monitor": ["-100123"],
            "product": {
                "name": "Test Product",
                "price": "100₽",
                "link": "https://test.com",
            },
            "triggers": {
                "respond_when": [
                    {"keywords": ["тест", "продукт"], "probability": 1.0}
                ],
                "ignore_when": [
                    {"contains": ["спам"], "from_bot": True}
                ],
            },
            "conversation_flow": {
                "group_mode": {
                    "max_messages_per_hour": 10,
                    "probability_to_respond": 0.5,
                    "style": "дружелюбный",
                },
                "dm_mode": {
                    "greeting": "Привет!",
                    "funnel": [{"step": "помочь"}],
                },
            },
            "anti_spam": {
                "min_delay_between_messages": 0,  # No delay in tests
                "max_delay_between_messages": 0,
                "typing_simulation": False,
                "random_typos": False,
            },
            "router_model": "openrouter/test-fast",
            "generator_model": "openrouter/test-slow",
        }
    }
    
    with open(p_dir / "persona.yaml", "w") as f:
        yaml.dump(data, f, allow_unicode=True)
    
    return str(tmp_path)


@pytest.fixture
def orchestrator(personas_dir, tmp_path):
    """Create orchestrator with test config."""
    return SalesBotOrchestrator(
        personas_dir=personas_dir,
        memory_dir=str(tmp_path / "memory"),
        openrouter_api_key="test-key",
    )


class TestOrchestratorLoading:
    """Test persona loading in orchestrator."""
    
    def test_load_personas(self, orchestrator):
        configs = orchestrator.load_personas()
        assert len(configs) == 1
        assert configs[0].name == "TestBot"
    
    def test_build_runtime(self, orchestrator):
        configs = orchestrator.load_personas()
        runtime = orchestrator._build_runtime(configs[0])
        
        assert runtime.config.name == "TestBot"
        assert runtime.router is not None
        assert runtime.generator is not None
        assert runtime.antispam is not None
        assert runtime.memory is not None
        assert runtime.dedup is not None
        assert runtime.llm is not None
    
    def test_persona_to_contract_conversion(self, orchestrator):
        """Persona config should convert to router contract format."""
        configs = orchestrator.load_personas()
        contract = orchestrator._build_router_contract(configs[0])
        
        assert "persona" in contract
        assert "product" in contract
        assert "triggers" in contract
        assert "conversation_flow" in contract
        
        assert contract["persona"]["name"] == "TestBot"
        assert len(contract["product"]["products"]) == 1
        assert contract["product"]["products"][0]["name"] == "Test Product"


class TestOrchestratorPipeline:
    """Test message processing pipeline."""
    
    @pytest.mark.asyncio
    async def test_handle_message_respond(self, orchestrator):
        """Test: message → router RESPOND → generator → send."""
        configs = orchestrator.load_personas()
        runtime = orchestrator._build_runtime(configs[0])
        orchestrator.runtimes["TestBot"] = runtime
        
        # Mock LLM responses
        runtime.llm.call = AsyncMock(side_effect=[
            MagicMock(
                text=json.dumps({
                    "decision": "RESPOND",
                    "confidence": 0.9,
                    "reason": "Тестовое сообщение",
                    "keywords": ["тест"],
                }),
                success=True,
            ),
            MagicMock(
                text=json.dumps({
                    "text": "Тестовый ответ",
                    "tone": "expert",
                    "stage": "engage",
                    "remember": [],
                }),
                success=True,
            ),
        ])
        
        # Disable random anti-detection behaviors for deterministic test
        runtime.antispam.leave_on_read_probability = 0.0
        runtime.antispam.emoji_reaction_probability = 0.0
        
        # Mock monitor (send)
        mock_monitor = MagicMock()
        mock_monitor.send_message = AsyncMock(return_value=True)
        runtime.monitor = mock_monitor
        
        msg = IncomingMessage(
            message_id=1,
            chat_id="-100123",
            chat_title="Test Chat",
            user_id="500",
            username="testuser",
            display_name="Test User",
            text="Тестовое сообщение",
            is_dm=False,
            date=1700000000,
            platform=Platform.TELEGRAM_USERBOT,
        )
        
        # Patch asyncio.sleep to avoid delays
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await orchestrator._handle_message(msg, runtime)
        
        assert runtime.stats["responses_sent"] == 1
        mock_monitor.send_message.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_handle_message_ignore(self, orchestrator):
        """Test: message → router IGNORE → no send."""
        configs = orchestrator.load_personas()
        runtime = orchestrator._build_runtime(configs[0])
        orchestrator.runtimes["TestBot"] = runtime
        
        runtime.llm.call = AsyncMock(return_value=MagicMock(
            text=json.dumps({
                "decision": "IGNORE",
                "confidence": 0.95,
                "reason": "Спам",
            }),
            success=True,
        ))
        
        mock_monitor = MagicMock()
        mock_monitor.send_message = AsyncMock()
        runtime.monitor = mock_monitor
        
        msg = IncomingMessage(
            message_id=1,
            chat_id="-100123",
            chat_title="Test",
            user_id="500",
            username="u",
            display_name="U",
            text="Спам сообщение",
            is_dm=False,
            date=1700000000,
        )
        
        await orchestrator._handle_message(msg, runtime)
        
        assert runtime.stats["ignored"] == 1
        mock_monitor.send_message.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_handle_dm_skips_router(self, orchestrator):
        """DM should skip router and go directly to generation."""
        configs = orchestrator.load_personas()
        runtime = orchestrator._build_runtime(configs[0])
        orchestrator.runtimes["TestBot"] = runtime
        
        runtime.llm.call = AsyncMock(return_value=MagicMock(
            text=json.dumps({
                "text": "Ответ в ЛС",
                "tone": "expert",
                "stage": "help",
                "remember": ["заинтересован"],
            }),
            success=True,
        ))
        
        mock_monitor = MagicMock()
        mock_monitor.send_message = AsyncMock(return_value=True)
        runtime.monitor = mock_monitor
        
        msg = IncomingMessage(
            message_id=2,
            chat_id="500",
            chat_title="DM",
            user_id="500",
            username="u",
            display_name="U",
            text="Расскажи подробнее",
            is_dm=True,
            date=1700000000,
        )
        
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await orchestrator._handle_message(msg, runtime)
        
        assert runtime.stats["responses_sent"] == 1
        # DM should only call LLM once (generator, not router)
        assert runtime.llm.call.call_count == 1
    
    @pytest.mark.asyncio
    async def test_deduplication(self, orchestrator):
        """Duplicate message should be skipped."""
        configs = orchestrator.load_personas()
        runtime = orchestrator._build_runtime(configs[0])
        orchestrator.runtimes["TestBot"] = runtime
        
        runtime.llm.call = AsyncMock(return_value=MagicMock(
            text=json.dumps({"decision": "RESPOND", "confidence": 0.9, "reason": "x"}),
            success=True,
        ))
        
        msg = IncomingMessage(
            message_id=1,
            chat_id="-100123",
            chat_title="Test",
            user_id="500",
            username="u",
            display_name="U",
            text="Same message",
            is_dm=False,
            date=1700000000,
        )
        
        # First call
        await orchestrator._handle_message(msg, runtime)
        first_count = runtime.stats["messages_processed"]
        
        # Second call — should be deduped
        runtime.llm.call.reset_mock()
        runtime.llm.call = AsyncMock(return_value=MagicMock(
            text=json.dumps({"decision": "RESPOND", "confidence": 0.9, "reason": "x"}),
            success=True,
        ))
        
        await orchestrator._handle_message(msg, runtime)
        
        # Messages processed counter still increments but LLM not called
        assert runtime.stats["messages_processed"] == first_count + 1


class TestOrchestratorStatus:
    """Test status reporting."""
    
    def test_get_status_empty(self, orchestrator):
        status = orchestrator.get_status()
        assert status["running"] is False
        assert status["personas"] == {}
    
    def test_get_status_with_runtime(self, orchestrator):
        configs = orchestrator.load_personas()
        runtime = orchestrator._build_runtime(configs[0])
        orchestrator.runtimes["TestBot"] = runtime
        
        status = orchestrator.get_status()
        assert "TestBot" in status["personas"]
        assert status["personas"]["TestBot"]["platform"] == "telegram"
        assert status["personas"]["TestBot"]["state"] == "idle"
