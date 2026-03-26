"""Regression: dedup SQLite files are per persona — same message_id must not cross stores."""

import pytest

from src.utils.dedup import DeduplicationStore


@pytest.mark.asyncio
async def test_two_dedup_stores_same_chat_message_isolated(tmp_path) -> None:
    """Marking processed in store A must not make store B treat the message as processed."""
    path_a = tmp_path / "persona_a" / "processed_messages.json"
    path_b = tmp_path / "persona_b" / "processed_messages.json"
    store_a = DeduplicationStore(storage_path=str(path_a))
    store_b = DeduplicationStore(storage_path=str(path_b))

    chat_id = "-1001"
    message_id = 42
    text = "same payload"

    assert not store_a.is_processed(chat_id, message_id, text)
    assert not store_b.is_processed(chat_id, message_id, text)

    await store_a.mark_processed(chat_id, message_id, text)

    assert store_a.is_processed(chat_id, message_id, text)
    assert not store_b.is_processed(chat_id, message_id, text)
