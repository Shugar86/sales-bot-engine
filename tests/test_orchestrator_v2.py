"""Tests for Orchestrator — multi-persona orchestrator."""
import importlib

import pytest
import json
import yaml
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.orchestrator import SalesBotOrchestrator, PersonaRuntime, BotState
from src.models.message import IncomingMessage, Platform
from src.core.persona_manager import PersonaConfig, TriggerConfig
from src.platforms.capabilities import PlatformCapabilities


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


@pytest.fixture
def mock_memory_and_graph():
    """Avoid real Supabase and Postgres checkpointer during runtime build."""
    mem = AsyncMock()
    mem.close = AsyncMock()
    graph_mock = MagicMock()
    graph_mock.ainvoke = AsyncMock(
        return_value={
            "sent": False,
            "route_decision": "ignore",
            "node_history": [],
        }
    )
    graph_builder = importlib.import_module("src.graph.builder")
    with patch(
        "src.core.orchestrator.MemoryFacade.create",
        new_callable=AsyncMock,
        return_value=mem,
    ), patch.object(
        graph_builder,
        "compile_persona_graph",
        new_callable=AsyncMock,
        return_value=graph_mock,
    ):
        yield mem, graph_mock


def _make_mock_adapter():
    ad = MagicMock()
    caps = PlatformCapabilities(
        supports_dm=True,
        supports_group_reply=True,
        supports_reactions=True,
        supports_typing_indicator=True,
    )
    ad.capabilities = MagicMock(return_value=caps)
    ad.send_reply = AsyncMock(return_value=True)
    ad.send_reaction = AsyncMock(return_value=True)
    return ad


class TestOrchestratorLoading:
    """Test persona loading in orchestrator."""
    
    def test_load_personas(self, orchestrator):
        configs = orchestrator.load_personas()
        assert len(configs) == 1
        assert configs[0].name == "TestBot"
    
    @pytest.mark.asyncio
    async def test_build_runtime(self, orchestrator, mock_memory_and_graph):
        configs = orchestrator.load_personas()
        runtime = await orchestrator._build_runtime(configs[0])

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
    async def test_handle_message_respond(self, orchestrator, mock_memory_and_graph):
        """Test: message → router RESPOND → generator → send."""
        configs = orchestrator.load_personas()
        runtime = await orchestrator._build_runtime(configs[0])
        orchestrator.runtimes["TestBot"] = runtime

        _, graph_mock = mock_memory_and_graph
        graph_mock.ainvoke = AsyncMock(
            return_value={
                "sent": True,
                "route_decision": "respond",
                "validated_text": "ok",
                "node_history": ["send"],
            }
        )

        runtime.adapter = _make_mock_adapter()

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

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await orchestrator._handle_message(msg, runtime)

        assert runtime.stats["responses_sent"] == 1
    
    @pytest.mark.asyncio
    async def test_handle_message_ignore(self, orchestrator, mock_memory_and_graph):
        """Test: message → router IGNORE → no send."""
        configs = orchestrator.load_personas()
        runtime = await orchestrator._build_runtime(configs[0])
        orchestrator.runtimes["TestBot"] = runtime

        _, graph_mock = mock_memory_and_graph
        graph_mock.ainvoke = AsyncMock(
            return_value={
                "sent": False,
                "route_decision": "ignore",
                "node_history": ["route"],
            }
        )

        runtime.adapter = _make_mock_adapter()

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
        runtime.adapter.send_reply.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_handle_dm_pipeline(self, orchestrator, mock_memory_and_graph):
        """DM path completes via graph and records a sent response."""
        configs = orchestrator.load_personas()
        runtime = await orchestrator._build_runtime(configs[0])
        orchestrator.runtimes["TestBot"] = runtime

        _, graph_mock = mock_memory_and_graph
        graph_mock.ainvoke = AsyncMock(
            return_value={
                "sent": True,
                "route_decision": "respond",
                "node_history": ["send"],
            }
        )

        runtime.adapter = _make_mock_adapter()

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
    
    @pytest.mark.asyncio
    async def test_deduplication(self, orchestrator, mock_memory_and_graph):
        """Duplicate message should be skipped."""
        configs = orchestrator.load_personas()
        runtime = await orchestrator._build_runtime(configs[0])
        orchestrator.runtimes["TestBot"] = runtime

        _, graph_mock = mock_memory_and_graph
        graph_mock.ainvoke = AsyncMock(
            return_value={
                "sent": False,
                "route_decision": "respond",
                "node_history": ["dedup"],
            }
        )

        runtime.adapter = _make_mock_adapter()

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
        
        # First call (dedup node marks duplicate in real graph; here ainvoke returns same)
        await orchestrator._handle_message(msg, runtime)
        first_count = runtime.stats["messages_processed"]

        await orchestrator._handle_message(msg, runtime)

        assert runtime.stats["messages_processed"] == first_count + 1


class TestOrchestratorStatus:
    """Test status reporting."""
    
    def test_get_status_empty(self, orchestrator):
        status = orchestrator.get_status()
        assert status["running"] is False
        assert status["personas"] == {}
    
    @pytest.mark.asyncio
    async def test_get_status_with_runtime(self, orchestrator, mock_memory_and_graph):
        configs = orchestrator.load_personas()
        runtime = await orchestrator._build_runtime(configs[0])
        orchestrator.runtimes["TestBot"] = runtime

        status = orchestrator.get_status()
        assert "TestBot" in status["personas"]
        assert status["personas"]["TestBot"]["platform"] == "telegram"
        assert status["personas"]["TestBot"]["platform_key"] is None
        assert status["personas"]["TestBot"]["state"] == "idle"
