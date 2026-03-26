"""
Telegram Monitor — long polling, fetch messages, send responses (Production)

Features:
- Persistent offset storage (survives restart)
- Retry with exponential backoff for send operations
- 429 rate limit handling with Retry-After
- Connection health checks
"""

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import httpx

from ..utils.logger import get_logger
from ..core.retry import retry_with_backoff, TELEGRAM_SEND_POLICY, TELEGRAM_API_POLICY

logger = get_logger("telegram")


@dataclass
class TelegramMessage:
    """Parsed Telegram message"""
    message_id: int
    chat_id: str
    chat_title: str
    user_id: str
    username: str
    display_name: str
    text: str
    is_dm: bool
    date: int
    reply_to_message_id: Optional[int] = None


class TelegramMonitor:
    """
    Telegram long polling монитор.
    
    Получает сообщения через getUpdates (long polling),
    парсит их и передаёт в callback для обработки.
    """
    
    def __init__(self, bot_token: str, poll_timeout: int = 30, storage_dir: str = None):
        self.token = bot_token
        self.poll_timeout = poll_timeout
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.offset = 0
        self._client: Optional[httpx.AsyncClient] = None
        self._running = False

        # Offset storage for persistence across restarts
        self._storage_dir = Path(storage_dir) if storage_dir else Path("data/memory")
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._offset_file = self._storage_dir / f"tg_offset_{self._get_bot_id()}.json"
        self._load_offset()

    def _get_bot_id(self) -> str:
        """Extract bot ID from token for filename."""
        if ":" in self.token:
            return self.token.split(":")[0]
        return "unknown"

    def _load_offset(self):
        """Load last known offset from storage."""
        try:
            if self._offset_file.exists():
                with open(self._offset_file, "r") as f:
                    data = json.load(f)
                    self.offset = data.get("offset", 0)
                    logger.info(f"Loaded offset: {self.offset}")
        except Exception as e:
            logger.warning(f"Could not load offset: {e}")
            self.offset = 0

    def _save_offset(self):
        """Save current offset to storage (atomic write)."""
        try:
            tmp_file = self._offset_file.with_suffix(".tmp")
            with open(tmp_file, "w") as f:
                json.dump({"offset": self.offset, "saved_at": time.time()}, f)
            tmp_file.replace(self._offset_file)
        except Exception as e:
            logger.warning(f"Could not save offset: {e}")
    
    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.poll_timeout + 10)
        return self._client
    
    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
    
    async def get_updates(self) -> list[dict]:
        """
        Long polling getUpdates with retry.

        Returns:
            List of updates from Telegram
        """
        async def _fetch():
            response = await self.client.get(
                f"{self.base_url}/getUpdates",
                params={
                    "offset": self.offset,
                    "timeout": self.poll_timeout,
                    "allowed_updates": '["message"]',
                },
            )

            if response.status_code == 409:
                logger.error("409 Conflict — another bot instance is running!")
                raise ConnectionError("409 Conflict - another bot instance running")

            if response.status_code == 429:
                retry_after = response.json().get("parameters", {}).get("retry_after", 30)
                logger.warning(f"Rate limited by Telegram, retry after {retry_after}s")
                await asyncio.sleep(retry_after)
                raise ConnectionError(f"429 Rate limited (retry_after: {retry_after})")

            response.raise_for_status()

            data = response.json()
            if not data.get("ok"):
                raise ValueError(f"getUpdates not ok: {data}")

            return data.get("result", [])

        try:
            updates = await retry_with_backoff(
                _fetch,
                policy=TELEGRAM_API_POLICY,
                name="get_updates",
            )

            # Update offset and save
            for update in updates:
                update_id = update.get("update_id", 0)
                if update_id >= self.offset:
                    self.offset = update_id + 1
            self._save_offset()

            return updates

        except httpx.TimeoutException:
            # Timeout is normal for long polling
            return []
        except Exception as e:
            logger.error(f"getUpdates error: {e}")
            return []
    
    def parse_update(self, update: dict) -> Optional[TelegramMessage]:
        """Парсинг обновления в TelegramMessage"""
        msg = update.get("message")
        if not msg:
            return None
        
        chat = msg.get("chat", {})
        from_user = msg.get("from", {})
        
        # Определяем тип чата
        chat_type = chat.get("type", "private")
        is_dm = chat_type == "private"
        
        return TelegramMessage(
            message_id=msg.get("message_id", 0),
            chat_id=str(chat.get("id", "")),
            chat_title=chat.get("title", "DM") if not is_dm else "DM",
            user_id=str(from_user.get("id", "")),
            username=from_user.get("username", ""),
            display_name=from_user.get("first_name", "") + " " + from_user.get("last_name", ""),
            text=msg.get("text", ""),
            is_dm=is_dm,
            date=msg.get("date", 0),
            reply_to_message_id=msg.get("reply_to_message", {}).get("message_id") if msg.get("reply_to_message") else None,
        )
    
    async def send_message(self, chat_id: str, text: str, reply_to: int = None) -> bool:
        """
        Send message with retry.

        Args:
            chat_id: Chat ID
            text: Message text
            reply_to: Message ID to reply to (optional)

        Returns:
            True if sent successfully
        """
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }

        if reply_to:
            payload["reply_to_message_id"] = reply_to

        async def _do_send():
            response = await self.client.post(
                f"{self.base_url}/sendMessage",
                json=payload,
            )

            if response.status_code == 429:
                retry_after = response.json().get("parameters", {}).get("retry_after", 30)
                logger.warning(f"Rate limited by Telegram, retry after {retry_after}s")
                await asyncio.sleep(retry_after)
                raise ConnectionError(f"429 Rate limited (retry_after: {retry_after})")

            response.raise_for_status()

            data = response.json()
            if not data.get("ok"):
                raise ValueError(f"sendMessage not ok: {data}")

            logger.info(f"Sent to {chat_id}: {text[:80]}...")
            return True

        try:
            return await retry_with_backoff(
                _do_send,
                policy=TELEGRAM_SEND_POLICY,
                name=f"send_message:{chat_id}",
            )
        except Exception as e:
            logger.error(f"sendMessage failed after retries: {e}")
            return False

    async def send_typing(self, chat_id: str) -> None:
        """Send typing chat action (best-effort)."""
        try:
            await self.client.post(
                f"{self.base_url}/sendChatAction",
                json={"chat_id": chat_id, "action": "typing"},
            )
        except Exception as e:
            logger.debug(f"sendChatAction typing failed: {e}")

    async def stop(self) -> None:
        """Signal poll loop to exit on next iteration."""
        self._running = False

    async def poll_loop(
        self,
        callback: Callable,
        allowed_chats: list[str] = None,
        interval: float = 1.0,
    ):
        """
        Main polling loop.

        Args:
            callback: async function (message: TelegramMessage) -> None
            allowed_chats: List of chat IDs to monitor (None = all)
            interval: Pause between poll requests when empty response
        """
        logger.info(f"Starting poll loop. Monitoring: {allowed_chats or 'ALL'}")
        self._running = True

        try:
            while self._running:
                try:
                    updates = await self.get_updates()

                    for update in updates:
                        msg = self.parse_update(update)
                        if not msg:
                            continue

                        # Filter by chat (DM always passes)
                        if not msg.is_dm and allowed_chats:
                            if msg.chat_id not in allowed_chats:
                                continue

                        try:
                            await callback(msg)
                        except Exception as e:
                            logger.error(f"Callback error for {msg.message_id}: {e}")

                    if not updates:
                        await asyncio.sleep(interval)

                except asyncio.CancelledError:
                    logger.info("Poll loop cancelled")
                    break
                except Exception as e:
                    logger.error(f"Poll loop error: {e}")
                    await asyncio.sleep(5)
        finally:
            self._running = False
            await self.close()
            logger.info("Poll loop ended, client closed")


async def test_connection(bot_token: str) -> dict:
    """
    Проверить подключение к Telegram Bot API.
    
    Returns:
        Информация о боте или ошибке
    """
    client = httpx.AsyncClient(timeout=10)
    try:
        response = await client.get(
            f"https://api.telegram.org/bot{bot_token}/getMe"
        )
        data = response.json()
        if data.get("ok"):
            result = data["result"]
            return {
                "ok": True,
                "id": result["id"],
                "username": result.get("username", ""),
                "first_name": result.get("first_name", ""),
                "can_join_groups": result.get("can_join_groups", False),
            }
        return {"ok": False, "error": data.get("description", "Unknown")}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        await client.aclose()
