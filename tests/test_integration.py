"""
LEGACY Integration Tests — v1 Single-Persona Path (orchestrator_legacy.py)

⚠️  NOTE: These tests cover the BOT_MODE=v1 legacy path using:
   - orchestrator_legacy.py (single-persona, Bot API)
   - contracts/loader.py (legacy contract format)
   - CONTRACT_PATH env var

For canonical runtime tests, see test_orchestrator_v2.py and test_orchestrator_integration.py
"""
import pytest
import json
import os
import asyncio
import yaml
from unittest.mock import AsyncMock, MagicMock, patch
from config.settings import AppConfig
from src.core.orchestrator_legacy import SalesBotOrchestrator, BotState
from src.core.router import Decision
from src.monitors.telegram_monitor import TelegramMessage


@pytest.fixture
def tmp_config(tmp_path):
    """Конфиг для тестов"""
    config = AppConfig()
    config.contract_path = str(tmp_path / "persona.yaml")
    config.memory.memory_dir = str(tmp_path / "memory")
    config.memory.log_dir = str(tmp_path / "logs")
    config.log_file = str(tmp_path / "logs" / "test.log")
    config.llm.api_key = "test-key"
    config.telegram.bot_token = "test-token"
    return config


@pytest.fixture
def contract_file(tmp_path):
    """Создать валидный контракт"""
    contract = {
        "persona": {
            "name": "Андрей",
            "backstory": "Кинолог-консультант",
            "speaking_style": {
                "tone": "Бывалый",
                "patterns": ["Коротко"],
                "forbidden": ["Спам"],
            }
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
    path = tmp_path / "persona.yaml"
    with open(path, "w") as f:
        yaml.dump(contract, f, allow_unicode=True)
    return str(path)


@pytest.fixture
def sample_message():
    return TelegramMessage(
        message_id=1, chat_id="100", chat_title="Kinology",
        user_id="500", username="doglover", display_name="Dog Lover",
        text="Подскажите хороший корм для овчарки с аллергией",
        is_dm=False, date=1700000000,
    )


@pytest.fixture
def dm_message():
    return TelegramMessage(
        message_id=2, chat_id="500", chat_title="DM",
        user_id="500", username="doglover", display_name="Dog Lover",
        text="А про гипоаллергенный подробнее можно?",
        is_dm=True, date=1700000100,
    )


def _mock_llm_chain(responses):
    """Helper: создаёт mock LLM с цепочкой ответов"""
    idx = [0]
    async def mock_call(model, prompt, **kwargs):
        i = idx[0]
        idx[0] += 1
        if i < len(responses):
            return responses[i]
        return MagicMock(text='{"text": "fallback"}', success=True)
    return mock_call


@pytest.mark.asyncio
async def test_respond_flow(tmp_config, contract_file, sample_message, monkeypatch):
    """E2E: сообщение в группе → роутер RESPOND → генератор → отправка"""
    tmp_config.contract_path = contract_file
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    
    mock_call = _mock_llm_chain([
        MagicMock(text=json.dumps({
            "decision": "RESPOND", "confidence": 0.9,
            "reason": "Кормление", "keywords": ["корм"]
        }), success=True),
        MagicMock(text=json.dumps({
            "text": "Попробуйте гипоаллергенный на ягнёнке",
            "tone": "expert", "stage": "engage", "remember": []
        }), success=True),
    ])
    
    with patch("src.core.orchestrator_legacy.LLMClient") as M:
        M.return_value.call = mock_call
        M.return_value.close = AsyncMock()
        with patch("src.core.orchestrator_legacy.TelegramMonitor") as T:
            T.return_value.send_message = AsyncMock(return_value=True)
            T.return_value.close = AsyncMock()
            
            bot = SalesBotOrchestrator(tmp_config)
            await bot.initialize()
            await bot.handle_message(sample_message)
            
            T.return_value.send_message.assert_called_once()
            text = T.return_value.send_message.call_args[1]["text"]
            assert "гипоаллергенный" in text
            assert bot.stats["responses_sent"] == 1


@pytest.mark.asyncio
async def test_ignore_flow(tmp_config, contract_file, sample_message, monkeypatch):
    """E2E: роутер IGNORE → не отправляем"""
    tmp_config.contract_path = contract_file
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    
    mock_call = _mock_llm_chain([
        MagicMock(text=json.dumps({
            "decision": "IGNORE", "confidence": 0.95, "reason": "Не по теме"
        }), success=True),
    ])
    
    with patch("src.core.orchestrator_legacy.LLMClient") as M:
        M.return_value.call = mock_call
        M.return_value.close = AsyncMock()
        with patch("src.core.orchestrator_legacy.TelegramMonitor") as T:
            T.return_value.send_message = AsyncMock()
            T.return_value.close = AsyncMock()
            
            bot = SalesBotOrchestrator(tmp_config)
            await bot.initialize()
            sample_message.text = "Что думаете про выборы?"
            await bot.handle_message(sample_message)
            
            T.return_value.send_message.assert_not_called()
            assert bot.stats["ignored"] == 1


@pytest.mark.asyncio
async def test_dm_with_group_context(tmp_config, contract_file, sample_message, dm_message, monkeypatch):
    """E2E: группа → ЛС, бот помнит контекст"""
    tmp_config.contract_path = contract_file
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    
    mock_call = _mock_llm_chain([
        # Group: route RESPOND
        MagicMock(text=json.dumps({"decision": "RESPOND", "confidence": 0.9, "reason": "test"}), success=True),
        # Group: generate
        MagicMock(text=json.dumps({"text": "Гипоаллергенный на ягнёнке", "tone": "expert", "stage": "engage", "remember": []}), success=True),
        # DM: generate with context
        MagicMock(text=json.dumps({"text": "Как я говорил, ягнёнок идеален для овчарок", "tone": "expert", "stage": "soft_sell", "remember": []}), success=True),
    ])
    
    with patch("src.core.orchestrator_legacy.LLMClient") as M:
        M.return_value.call = mock_call
        M.return_value.close = AsyncMock()
        with patch("src.core.orchestrator_legacy.TelegramMonitor") as T:
            T.return_value.send_message = AsyncMock(return_value=True)
            T.return_value.close = AsyncMock()
            
            bot = SalesBotOrchestrator(tmp_config)
            await bot.initialize()
            
            await bot.handle_message(sample_message)
            await bot.handle_message(dm_message)
            
            assert bot.stats["responses_sent"] == 2


@pytest.mark.asyncio
async def test_deduplication(tmp_config, contract_file, sample_message, monkeypatch):
    """Дубликат сообщения пропускается"""
    tmp_config.contract_path = contract_file
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    
    mock_call = _mock_llm_chain([
        MagicMock(text=json.dumps({"decision": "RESPOND", "confidence": 0.9, "reason": "test"}), success=True),
        MagicMock(text=json.dumps({"text": "Ответ", "tone": "expert", "stage": "engage", "remember": []}), success=True),
    ])
    
    with patch("src.core.orchestrator_legacy.LLMClient") as M:
        M.return_value.call = mock_call
        M.return_value.close = AsyncMock()
        with patch("src.core.orchestrator_legacy.TelegramMonitor") as T:
            T.return_value.send_message = AsyncMock(return_value=True)
            T.return_value.close = AsyncMock()
            
            bot = SalesBotOrchestrator(tmp_config)
            await bot.initialize()
            
            await bot.handle_message(sample_message)
            first_count = bot.stats["messages_processed"]
            
            await bot.handle_message(sample_message)
            assert bot.stats["messages_processed"] == 2


@pytest.mark.asyncio
async def test_get_status(tmp_config, contract_file):
    tmp_config.contract_path = contract_file
    
    with patch("src.core.orchestrator_legacy.LLMClient") as M:
        M.return_value.close = AsyncMock()
        bot = SalesBotOrchestrator(tmp_config)
        await bot.initialize()
        
        status = bot.get_status()
        assert status["state"] == "idle"
        assert status["contract"] == "Андрей"
