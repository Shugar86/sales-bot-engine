"""
Telegram Userbot Monitor — Telethon-based (Production)

Reads ALL messages in groups (no need to add bot).
Sends as a regular user account.

Features:
- Reconnection with exponential backoff
- Retry for send operations
- Proper graceful shutdown
"""

import asyncio
import os
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..utils.logger import get_logger
from ..core.retry import retry_with_backoff, TELEGRAM_SEND_POLICY

logger = get_logger("telegram-userbot")

# Telethon is optional — import gracefully
try:
    from telethon import TelegramClient, events
    from telethon.tl.types import Channel, Chat, User
    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False
    logger.warning("Telethon not installed. Run: pip install telethon")


@dataclass
class UserbotMessage:
    """Parsed Telegram message from userbot"""
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
    is_reply_to_me: bool = False
    raw: object = None  # original Telethon message


class TelegramUserbot:
    """
    Telegram Userbot via Telethon.
    
    Features:
    - Monitors groups as a regular user (no admin needed)
    - Reads ALL messages (not just when mentioned)
    - Sends messages as a user (not a bot)
    - Supports multiple sessions (multi-persona)
    """
    
    def __init__(
        self,
        session_name: str,
        api_id: int = None,
        api_hash: str = None,
        phone: str = None,
    ):
        if not TELETHON_AVAILABLE:
            raise ImportError("Telethon is required: pip install telethon")
        
        self.session_name = session_name
        self.api_id = api_id or int(os.getenv("TELEGRAM_API_ID", "0"))
        self.api_hash = api_hash or os.getenv("TELEGRAM_API_HASH", "")
        self.phone = phone or os.getenv("TELEGRAM_PHONE", "")
        
        # Session dir
        session_dir = os.getenv("SESSION_DIR", "./sessions")
        os.makedirs(session_dir, exist_ok=True)
        session_path = os.path.join(session_dir, session_name)
        
        self.client = TelegramClient(session_path, self.api_id, self.api_hash)
        self._callback: Optional[Callable] = None
        self._allowed_chats: list[str] = []
        self._my_id: Optional[int] = None
        self._running = False
    
    async def start(self):
        """Start the userbot client."""
        await self.client.start(phone=self.phone)
        me = await self.client.get_me()
        self._my_id = me.id
        logger.info(f"Userbot started: {me.first_name} (@{me.username or 'no_username'})")
        return me
    
    async def stop(self):
        """Stop the userbot client gracefully."""
        self._running = False
        try:
            # Disconnect will break run_until_disconnected()
            await self.client.disconnect()
            logger.info("Userbot stopped")
        except Exception as e:
            logger.warning(f"Error during userbot stop: {e}")

    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self.client.is_connected()

    async def reconnect(self) -> bool:
        """Attempt to reconnect the client."""
        try:
            if not self.client.is_connected():
                await self.client.connect()
                if not await self.client.is_user_authorized():
                    logger.error("Client not authorized after reconnect")
                    return False
                logger.info("Userbot reconnected successfully")
                return True
            return True
        except Exception as e:
            logger.error(f"Reconnection failed: {e}")
            return False
    
    def _parse_message(self, event) -> Optional[UserbotMessage]:
        """Parse Telethon event into UserbotMessage."""
        msg = event.message
        if not msg or not msg.text:
            return None
        
        sender = msg.sender
        chat = msg.chat
        
        if chat is None:
            return None
        
        # Determine chat info using duck typing (class name check)
        # Works even without Telethon installed (for testing)
        chat_type = type(chat).__name__
        
        if chat_type == "Channel":
            chat_id = str(chat.id)
            chat_title = getattr(chat, "title", None) or "Channel"
            is_dm = False
        elif chat_type == "Chat":
            chat_id = str(chat.id)
            chat_title = getattr(chat, "title", None) or "Group"
            is_dm = False
        elif chat_type == "User":
            chat_id = str(chat.id)
            chat_title = f"DM:{getattr(chat, 'first_name', '') or ''}"
            is_dm = not getattr(chat, "bot", False)
        else:
            # Unknown chat type — try to extract id
            if hasattr(chat, "id"):
                chat_id = str(chat.id)
                chat_title = getattr(chat, "title", None) or f"Unknown:{chat_id}"
                is_dm = False
            else:
                return None
        
        # Determine sender info
        if sender:
            user_id = str(sender.id)
            username = getattr(sender, "username", "") or ""
            display_name = (getattr(sender, "first_name", "") or "") + " " + (getattr(sender, "last_name", "") or "")
        else:
            user_id = "unknown"
            username = ""
            display_name = "Unknown"
        
        # Check if replying to me
        is_reply_to_me = False
        if msg.reply_to and msg.reply_to.reply_to_msg_id:
            # Will be checked later against my messages
            pass
        
        return UserbotMessage(
            message_id=msg.id,
            chat_id=chat_id,
            chat_title=chat_title,
            user_id=user_id,
            username=username.strip(),
            display_name=display_name.strip(),
            text=msg.text,
            is_dm=is_dm,
            date=int(msg.date.timestamp()) if msg.date else 0,
            reply_to_message_id=msg.reply_to.reply_to_msg_id if msg.reply_to else None,
            is_reply_to_me=is_reply_to_me,
            raw=msg,
        )
    
    async def send_message(
        self,
        chat_id: str,
        text: str,
        reply_to: int = None,
        typing_delay: bool = True,
    ) -> bool:
        """
        Send message as user. Simulates typing with retry.

        Args:
            chat_id: Chat/entity ID
            text: Message text
            reply_to: Message ID to reply to
            typing_delay: Simulate typing (human-like)

        Returns:
            True if sent successfully
        """
        async def _do_send():
            # Ensure connected
            if not self.client.is_connected():
                await self.reconnect()

            # Resolve entity
            entity = await self.client.get_entity(
                int(chat_id) if chat_id.lstrip('-').isdigit() else chat_id
            )

            # Simulate typing
            if typing_delay:
                chars_per_sec = random.uniform(30, 80)
                typing_time = min(len(text) / chars_per_sec, 15)
                typing_time = max(typing_time, 1.5)

                async with self.client.action(entity, 'typing'):
                    await asyncio.sleep(typing_time)

            # Send
            await self.client.send_message(entity, text, reply_to=reply_to)
            logger.info(f"Sent to {chat_id}: {text[:80]}...")
            return True

        try:
            return await retry_with_backoff(
                _do_send,
                policy=TELEGRAM_SEND_POLICY,
                name=f"send_message:{chat_id}",
            )
        except Exception as e:
            logger.error(f"send_message failed after retries: {e}")
            return False

    async def send_typing(self, chat_id: str) -> None:
        """Trigger typing indicator briefly (graph applies duration separately)."""
        if not TELETHON_AVAILABLE:
            return
        try:
            entity = await self.client.get_entity(
                int(chat_id) if chat_id.lstrip("-").isdigit() else chat_id
            )
            async with self.client.action(entity, "typing"):
                await asyncio.sleep(0.05)
        except Exception as e:
            logger.debug(f"send_typing: {e}")

    async def send_reaction(
        self,
        chat_id: str,
        message_id: int,
        emoji: str = "👍",
    ) -> bool:
        """
        Send emoji reaction to a message.
        
        Args:
            chat_id: Chat/entity ID
            message_id: Message ID to react to
            emoji: Emoji to react with (default: 👍)
        """
        if not TELETHON_AVAILABLE:
            logger.warning("Telethon not available — cannot send reaction")
            return False
        
        try:
            entity = await self.client.get_entity(
                int(chat_id) if chat_id.lstrip('-').isdigit() else chat_id
            )
            
            await self.client.send_reaction(
                entity=entity,
                message=message_id,
                reaction=emoji,
            )
            logger.info(f"Sent reaction {emoji} to message {message_id} in {chat_id}")
            return True
            
        except Exception as e:
            logger.error(f"send_reaction error: {e}")
            return False
    
    async def get_chat_history(
        self,
        chat_id: str,
        limit: int = 10,
    ) -> list[dict]:
        """
        Read last N messages from chat for context.
        
        Args:
            chat_id: Chat/entity ID
            limit: Number of messages to read (default 10)
        
        Returns:
            List of message dicts with user_id, username, text, timestamp, etc.
        """
        if not TELETHON_AVAILABLE:
            logger.warning("Telethon not available — cannot read chat history")
            return []
        
        try:
            entity = await self.client.get_entity(
                int(chat_id) if chat_id.lstrip('-').isdigit() else chat_id
            )
            
            messages = []
            async for msg in self.client.iter_messages(entity, limit=limit):
                if not msg.text:
                    continue
                
                sender = await msg.get_sender()
                username = getattr(sender, "username", "") or ""
                display_name = ""
                if sender:
                    display_name = getattr(sender, "first_name", "") or ""
                    last_name = getattr(sender, "last_name", "") or ""
                    if last_name:
                        display_name = f"{display_name} {last_name}"
                
                messages.append({
                    "message_id": msg.id,
                    "user_id": str(msg.sender_id or ""),
                    "username": username,
                    "display_name": display_name,
                    "text": msg.text or "",
                    "timestamp": int(msg.date.timestamp()) if msg.date else 0,
                    "reply_to_message_id": msg.reply_to.reply_to_msg_id if msg.reply_to else None,
                })
            
            # Reverse to chronological order (oldest first)
            messages.reverse()
            return messages
            
        except Exception as e:
            logger.error(f"get_chat_history error: {e}")
            return []
    
    async def _handle_new_message(self, event):
        """Handle incoming message event."""
        try:
            msg = self._parse_message(event)
            if not msg:
                return
            
            # Skip my own messages
            if msg.user_id == str(self._my_id):
                return
            
            # Skip other bots (anti-bot-to-bot loop)
            sender = event.message.sender
            if sender and getattr(sender, "bot", False):
                logger.debug(f"Skipping bot message from {msg.user_id}")
                return
            
            # Filter by allowed chats (DM always passes)
            if not msg.is_dm and self._allowed_chats:
                if msg.chat_id not in self._allowed_chats:
                    return
            
            # Check if message is a reply to me
            if msg.reply_to_message_id:
                try:
                    replied_msg = await self.client.get_messages(
                        int(msg.chat_id),
                        ids=msg.reply_to_message_id
                    )
                    if replied_msg and replied_msg.sender_id == self._my_id:
                        msg.is_reply_to_me = True
                except Exception as e:
                    logger.debug(f"Could not check reply-to: {e}")
            
            # Call the handler
            if self._callback:
                await self._callback(msg)
                
        except Exception as e:
            logger.error(f"handle_new_message error: {e}")
    
    async def run(
        self,
        callback: Callable,
        allowed_chats: list[str] = None,
    ):
        """
        Main loop. Monitors all incoming messages.
        
        Args:
            callback: async function (UserbotMessage) -> None
            allowed_chats: List of chat IDs to monitor (None = all)
        """
        if not self.client.is_connected():
            await self.start()
        
        self._callback = callback
        self._allowed_chats = allowed_chats or []
        self._running = True
        
        # Register event handler for new messages
        self.client.on(events.NewMessage())(self._handle_new_message)
        
        logger.info(f"Monitoring chats: {self._allowed_chats or 'ALL'}")

        # Run with reconnection loop
        while self._running:
            try:
                await self.client.run_until_disconnected()
                if not self._running:
                    break
                # Disconnected but still running - try to reconnect
                logger.warning("Disconnected, attempting reconnect in 5s...")
                await asyncio.sleep(5)
                if not await self.reconnect():
                    logger.error("Failed to reconnect, will retry in 30s")
                    await asyncio.sleep(30)
            except Exception as e:
                if not self._running:
                    break
                logger.error(f"Connection error: {e}, retrying in 10s...")
                await asyncio.sleep(10)


class TelegramUserbotMulti:
    """
    Manages multiple Telegram userbots (one per persona).
    
    Each persona has its own session and can monitor different groups.
    """
    
    def __init__(self):
        self.bots: dict[str, TelegramUserbot] = {}
    
    def add_persona(
        self,
        persona_name: str,
        session_name: str,
        api_id: int = None,
        api_hash: str = None,
        phone: str = None,
    ) -> TelegramUserbot:
        """Add a persona with its own Telegram session."""
        bot = TelegramUserbot(
            session_name=session_name,
            api_id=api_id,
            api_hash=api_hash,
            phone=phone,
        )
        self.bots[persona_name] = bot
        logger.info(f"Added persona: {persona_name} (session: {session_name})")
        return bot
    
    async def start_all(self):
        """Start all userbots."""
        for name, bot in self.bots.items():
            try:
                await bot.start()
                logger.info(f"Started persona: {name}")
            except Exception as e:
                logger.error(f"Failed to start {name}: {e}")
    
    async def stop_all(self):
        """Stop all userbots."""
        for name, bot in self.bots.items():
            try:
                await bot.stop()
                logger.info(f"Stopped persona: {name}")
            except Exception as e:
                logger.error(f"Failed to stop {name}: {e}")


# --- Auth helper ---
async def create_session(session_name: str):
    """
    Interactive: creates a new Telethon session.
    Will ask for phone number and code in terminal.
    """
    if not TELETHON_AVAILABLE:
        raise ImportError("Telethon required")
    
    api_id = int(os.getenv("TELEGRAM_API_ID", "0"))
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    
    if not api_id or not api_hash:
        raise ValueError("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env")
    
    session_dir = os.getenv("SESSION_DIR", "./sessions")
    os.makedirs(session_dir, exist_ok=True)
    session_path = os.path.join(session_dir, session_name)
    
    client = TelegramClient(session_path, api_id, api_hash)
    await client.start()
    me = await client.get_me()
    print(f"✅ Session created: {me.first_name} (@{me.username})")
    await client.disconnect()
