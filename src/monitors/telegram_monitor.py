"""
Telegram Monitor — long polling, fetch messages, send responses
"""

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Callable, Optional

import httpx

from ..utils.logger import get_logger

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
    
    def __init__(self, bot_token: str, poll_timeout: int = 30):
        self.token = bot_token
        self.poll_timeout = poll_timeout
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.offset = 0
        self._client: Optional[httpx.AsyncClient] = None
    
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
        Long polling getUpdates.
        
        Returns:
            Список обновлений от Telegram
        """
        try:
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
                return []
            
            if response.status_code != 200:
                logger.error(f"getUpdates failed: {response.status_code} {response.text[:200]}")
                return []
            
            data = response.json()
            if not data.get("ok"):
                logger.error(f"getUpdates not ok: {data}")
                return []
            
            updates = data.get("result", [])
            
            # Обновляем offset
            for update in updates:
                update_id = update.get("update_id", 0)
                if update_id >= self.offset:
                    self.offset = update_id + 1
            
            return updates
            
        except httpx.TimeoutException:
            # Таймаут — нормально для long polling
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
        Отправить сообщение.
        
        Args:
            chat_id: ID чата
            text: Текст сообщения
            reply_to: ID сообщения для reply (опционально)
        
        Returns:
            True если отправлено успешно
        """
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        
        try:
            response = await self.client.post(
                f"{self.base_url}/sendMessage",
                json=payload,
            )
            
            if response.status_code == 429:
                retry_after = response.json().get("parameters", {}).get("retry_after", 30)
                logger.warning(f"Rate limited by Telegram, retry after {retry_after}s")
                await asyncio.sleep(retry_after)
                return False
            
            if response.status_code != 200:
                logger.error(f"sendMessage failed: {response.status_code} {response.text[:200]}")
                return False
            
            data = response.json()
            if data.get("ok"):
                logger.info(f"Sent to {chat_id}: {text[:80]}...")
                return True
            
            logger.error(f"sendMessage not ok: {data}")
            return False
            
        except Exception as e:
            logger.error(f"sendMessage error: {e}")
            return False
    
    async def poll_loop(
        self,
        callback: Callable,
        allowed_chats: list[str] = None,
        interval: float = 1.0,
    ):
        """
        Главный цикл polling.
        
        Args:
            callback: async функция (message: TelegramMessage) -> None
            allowed_chats: Список ID чатов для мониторинга (None = все)
            interval: Пауза между poll запросами при пустом ответе
        """
        logger.info(f"Starting poll loop. Monitoring: {allowed_chats or 'ALL'}")
        
        while True:
            try:
                updates = await self.get_updates()
                
                for update in updates:
                    msg = self.parse_update(update)
                    if not msg:
                        continue
                    
                    # Фильтр по чатам (для групп; DM всегда пропускаем)
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
                await asyncio.sleep(5)  # Пауза при ошибке


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
