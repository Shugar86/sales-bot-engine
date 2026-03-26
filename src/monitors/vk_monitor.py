"""
VK Monitor — Long Poll based
Monitors VK groups, sends messages in chats and DMs.
"""

import asyncio
import os
import random
from dataclasses import dataclass
from typing import Callable, Optional

from ..utils.logger import get_logger

logger = get_logger("vk-monitor")

try:
    import vk_api
    from vk_api.longpoll import VkLongPoll, VkEventType
    VK_AVAILABLE = True
except ImportError:
    VK_AVAILABLE = False
    logger.warning("vk_api not installed. Run: pip install vk_api")


@dataclass
class VKMessage:
    """Parsed VK message"""
    message_id: int
    chat_id: str       # peer_id (group negative, user positive)
    chat_title: str
    user_id: str
    username: str
    display_name: str
    text: str
    is_dm: bool
    date: int
    is_reply_to_me: bool = False


class VKMonitor:
    """
    VK Long Poll monitor.
    
    Monitors group conversations and DMs.
    """
    
    def __init__(self, access_token: str = None):
        if not VK_AVAILABLE:
            raise ImportError("vk_api required: pip install vk_api")
        
        self.token = access_token or os.getenv("VK_ACCESS_TOKEN", "")
        self.vk_session = None
        self.vk = None
        self.longpoll = None
        self._callback: Optional[Callable] = None
        self._allowed_chats: list[str] = []
        self._my_id: Optional[int] = None
    
    def start(self):
        """Start VK session."""
        self.vk_session = vk_api.VkApi(token=self.token)
        self.vk = self.vk_session.get_api()
        self.longpoll = VkLongPoll(self.vk_session)
        
        # Get my info
        account = self.vk.account.getProfileInfo()
        self._my_id = account["id"]
        logger.info(f"VK started: {account['first_name']} {account['last_name']}")
        return account
    
    def send_message(self, peer_id: str, text: str, reply_to: int = None) -> bool:
        """
        Send message in VK.
        
        Args:
            peer_id: Peer ID (user_id for DM, 2000000000+chat_id for groups)
            text: Message text
            reply_to: Message ID to reply to
        """
        try:
            params = {
                "peer_id": int(peer_id),
                "message": text,
                "random_id": random.randint(0, 2**31),
            }
            if reply_to:
                params["reply_to"] = reply_to
            
            self.vk.messages.send(**params)
            logger.info(f"Sent to {peer_id}: {text[:80]}...")
            return True
            
        except Exception as e:
            logger.error(f"send_message error: {e}")
            return False
    
    def _get_chat_title(self, peer_id: int) -> str:
        """Get chat/conversation title."""
        try:
            if peer_id > 2000000000:
                # Group chat
                chat_id = peer_id - 2000000000
                chat = self.vk.messages.getChat(chat_id=chat_id)
                return chat.get("title", f"Chat {chat_id}")
            elif peer_id < 0:
                # Community
                group = self.vk.groups.getById(group_id=abs(peer_id))
                return group[0]["name"] if group else f"Group {abs(peer_id)}"
            else:
                # User DM
                user = self.vk.users.get(user_id=peer_id)
                return f"{user[0]['first_name']} {user[0]['last_name']}" if user else f"User {peer_id}"
        except Exception:
            return str(peer_id)
    
    def _get_user_name(self, user_id: int) -> tuple[str, str]:
        """Get username and display name."""
        try:
            users = self.vk.users.get(user_ids=user_id)
            if users:
                u = users[0]
                return u.get("screen_name", ""), f"{u['first_name']} {u['last_name']}"
        except Exception:
            pass
        return "", f"User {user_id}"
    
    def run(self, callback: Callable, allowed_chats: list[str] = None):
        """
        Main loop. Monitors VK messages via Long Poll.
        
        Args:
            callback: function (VKMessage) -> None (sync!)
            allowed_chats: List of peer_ids to monitor (None = all)
        """
        if not self.longpoll:
            self.start()
        
        self._callback = callback
        self._allowed_chats = allowed_chats or []
        
        logger.info(f"VK monitoring: {self._allowed_chats or 'ALL'}")
        
        for event in self.longpoll.listen():
            if event.type == VkEventType.MESSAGE_NEW and event.to_me:
                try:
                    is_dm = event.peer_id < 2000000000 and event.peer_id > 0
                    
                    # Filter
                    if not is_dm and self._allowed_chats:
                        if str(event.peer_id) not in self._allowed_chats:
                            continue
                    
                    username, display_name = self._get_user_name(event.user_id)
                    
                    msg = VKMessage(
                        message_id=event.message_id,
                        chat_id=str(event.peer_id),
                        chat_title=self._get_chat_title(event.peer_id),
                        user_id=str(event.user_id),
                        username=username,
                        display_name=display_name,
                        text=event.text or "",
                        is_dm=is_dm,
                        date=event.timestamp,
                    )
                    
                    # Skip own messages
                    if str(event.user_id) == str(self._my_id):
                        continue
                    
                    if self._callback:
                        self._callback(msg)
                        
                except Exception as e:
                    logger.error(f"VK event handling error: {e}")


class VKMonitorAsync:
    """
    Async wrapper for VK monitor using vk-async (if available).
    Falls back to sync VK wrapped in asyncio.to_thread.
    """
    
    def __init__(self, access_token: str = None):
        self.sync_monitor = VKMonitor(access_token)
        self._callback_async: Optional[Callable] = None
    
    async def start(self):
        """Start in thread."""
        await asyncio.to_thread(self.sync_monitor.start)
    
    async def send_message(self, peer_id: str, text: str, reply_to: int = None) -> bool:
        """Send message async."""
        return await asyncio.to_thread(self.sync_monitor.send_message, peer_id, text, reply_to)
    
    async def run(self, callback: Callable, allowed_chats: list[str] = None):
        """
        Async message loop.
        Wraps sync Long Poll in async executor.
        """
        self._callback_async = callback
        
        def _sync_callback(msg: VKMessage):
            if self._callback_async:
                asyncio.ensure_future(self._callback_async(msg))
        
        await asyncio.to_thread(self.sync_monitor.run, _sync_callback, allowed_chats)
