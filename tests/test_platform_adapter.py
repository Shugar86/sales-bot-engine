"""Unit tests for platform registry and send options."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.core.persona_manager import PersonaConfig, AntiSpamConfig, GroupModeConfig
from src.models.message import IncomingMessage, Platform
from src.platforms import UnknownPlatformError, SendOptions, create_adapter
from src.platforms.adapters.vk import VKAdapter


@pytest.mark.asyncio
async def test_create_adapter_unknown_platform():
    config = PersonaConfig(
        name="x",
        platform="reddit",
        account_type="bot",
        anti_spam=AntiSpamConfig(),
        group_mode=GroupModeConfig(max_messages_per_hour=1),
    )
    with pytest.raises(UnknownPlatformError):
        await create_adapter(config)


def test_send_options_frozen():
    opts = SendOptions(reply_to_message_id=42, typing_already_simulated=True)
    assert opts.reply_to_message_id == 42
    assert opts.typing_already_simulated is True


@pytest.mark.asyncio
async def test_vk_adapter_send_reply_uses_peer_id():
    """VKAdapter should pass msg.chat_id as peer_id to the underlying monitor."""
    mock_inner = MagicMock()
    mock_inner.send_message = AsyncMock(return_value=True)
    adapter = VKAdapter(mock_inner, "test")

    msg = IncomingMessage(
        message_id=1,
        chat_id="2000000001",
        chat_title="Chat",
        user_id="u1",
        username="u",
        display_name="U",
        text="hi",
        is_dm=False,
        date=0,
        platform=Platform.VK,
    )
    ok = await adapter.send_reply(
        msg,
        "reply text",
        SendOptions(reply_to_message_id=99, typing_already_simulated=True),
    )
    assert ok is True
    mock_inner.send_message.assert_awaited_once_with(
        "2000000001",
        "reply text",
        reply_to=99,
    )
