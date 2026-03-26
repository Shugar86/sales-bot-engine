"""
Memory Writer — записывает в память после каждого взаимодействия.

Запоминает:
- Кто со мной говорил
- Что обсуждали
- Детали (порода собаки, проблемы, etc.)
- Стадию воронки
- Из какого чата пришёл (для DM)
- Моё последнее сообщение в чате (для anti-spam)

Философия:
- Память = "я знаю этого человека"
- В следующий раз могу сказать "上次 ты говорил что у тебя лабрадор"
- Без памяти бот — амнезик, каждое сообщение = первый контакт
"""

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class InteractionRecord:
    """Запись об одном взаимодействии."""
    timestamp: int
    chat_id: str
    chat_title: str
    user_message: str
    bot_response: str
    interaction_type: str  # "group" | "dm" | "reaction"
    topic: str = ""
    vibe: str = ""


@dataclass
class UserMemoryProfile:
    """Профиль памяти о пользователе."""
    user_id: str
    username: str = ""
    display_name: str = ""
    first_seen: int = 0
    last_seen: int = 0
    interaction_count: int = 0
    source_chat_id: str = ""        # откуда пришёл (для DM)
    source_chat_title: str = ""     # название исходного чата
    funnel_stage: str = "unknown"   # unknown/interested/asking/ready_to_buy/bought
    topics_discussed: list[str] = field(default_factory=list)
    personal_details: dict = field(default_factory=dict)  # имя собаки, порода, etc.
    notes: list[str] = field(default_factory=list)
    group_chats: list[str] = field(default_factory=list)  # в каких чатах общались
    has_dm: bool = False
    last_interaction_summary: str = ""


class MemoryWriter:
    """
    Записывает и читает память о пользователях.
    
    Хранит в JSON файлах, один файл на пользователя.
    Поддерживает:
    - Запись групповых взаимодействий
    - Запись DM взаимодействий
    - Запись эмодзи-реакций
    - Запоминание деталей пользователя
    - Трекинг воронки
    - Отслеживание source chat (из какого чата пришёл в DM)
    """
    
    def __init__(self, memory_dir: str = "data/memory"):
        """
        Args:
            memory_dir: Директория для хранения файлов памяти.
        """
        self.memory_dir = memory_dir
        os.makedirs(memory_dir, exist_ok=True)
        self._cache: dict[str, dict] = {}
    
    def _path(self, user_id: str) -> str:
        return os.path.join(self.memory_dir, f"{user_id}.json")
    
    def _load(self, user_id: str) -> dict:
        """Загрузить данные пользователя."""
        if user_id in self._cache:
            return self._cache[user_id]
        
        path = self._path(user_id)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._cache[user_id] = data
                return data
            except Exception:
                pass
        
        # Создаём нового
        now = int(time.time())
        data = {
            "user_id": user_id,
            "username": "",
            "display_name": "",
            "first_seen": now,
            "last_seen": now,
            "interaction_count": 0,
            "source_chat_id": "",
            "source_chat_title": "",
            "funnel_stage": "unknown",
            "topics_discussed": [],
            "personal_details": {},
            "notes": [],
            "group_chats": [],
            "has_dm": False,
            "last_interaction_summary": "",
            "interactions": [],
            "my_last_messages": {},  # chat_id -> timestamp
        }
        self._cache[user_id] = data
        return data
    
    def _save(self, user_id: str):
        """Сохранить данные пользователя."""
        path = self._path(user_id)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._cache[user_id], f, ensure_ascii=False, indent=2)
        except Exception:
            pass  # Fail silently in memory writes
    
    def write_group_interaction(
        self,
        user_id: str,
        username: str,
        display_name: str,
        chat_id: str,
        chat_title: str,
        user_message: str,
        bot_response: str,
        topic: str = "",
        vibe: str = "",
    ):
        """Записать групповое взаимодействие."""
        data = self._load(user_id)
        now = int(time.time())
        
        data["username"] = username or data.get("username", "")
        data["display_name"] = display_name or data.get("display_name", "")
        data["last_seen"] = now
        data["interaction_count"] = data.get("interaction_count", 0) + 1
        
        # Добавляем чат в список
        if chat_id not in data.get("group_chats", []):
            data.setdefault("group_chats", []).append(chat_id)
        
        # Записываем взаимодействие
        data.setdefault("interactions", []).append({
            "timestamp": now,
            "chat_id": chat_id,
            "chat_title": chat_title,
            "user_message": user_message[:500],
            "bot_response": bot_response[:500],
            "type": "group",
            "topic": topic,
            "vibe": vibe,
        })
        
        # Обрезаем историю до 20 записей
        data["interactions"] = data["interactions"][-20:]
        
        # Обновляем темы
        if topic and topic not in data.get("topics_discussed", []):
            data.setdefault("topics_discussed", []).append(topic)
        
        # Обновляем summary
        data["last_interaction_summary"] = f"Группа {chat_title}: {user_message[:100]}"
        
        self._save(user_id)
    
    def write_dm_interaction(
        self,
        user_id: str,
        username: str,
        display_name: str,
        user_message: str,
        bot_response: str,
        source_chat_id: str = "",
        source_chat_title: str = "",
        funnel_stage: str = "",
        topic: str = "",
    ):
        """Записать DM взаимодействие."""
        data = self._load(user_id)
        now = int(time.time())
        
        data["username"] = username or data.get("username", "")
        data["display_name"] = display_name or data.get("display_name", "")
        data["last_seen"] = now
        data["has_dm"] = True
        data["interaction_count"] = data.get("interaction_count", 0) + 1
        
        # Запоминаем source chat (из какого чата пришёл)
        if source_chat_id and not data.get("source_chat_id"):
            data["source_chat_id"] = source_chat_id
            data["source_chat_title"] = source_chat_title
        
        # Обновляем воронку
        if funnel_stage:
            data["funnel_stage"] = funnel_stage
        
        # Записываем взаимодействие
        data.setdefault("interactions", []).append({
            "timestamp": now,
            "chat_id": "dm",
            "chat_title": "DM",
            "user_message": user_message[:500],
            "bot_response": bot_response[:500],
            "type": "dm",
            "topic": topic,
            "funnel_stage": funnel_stage,
        })
        
        data["interactions"] = data["interactions"][-20:]
        
        # Обновляем summary
        data["last_interaction_summary"] = f"DM: {user_message[:100]}"
        
        self._save(user_id)
    
    def write_reaction(
        self,
        user_id: str,
        chat_id: str,
        emoji: str,
    ):
        """Записать эмодзи-реакцию."""
        data = self._load(user_id)
        now = int(time.time())
        
        data["last_seen"] = now
        data["interaction_count"] = data.get("interaction_count", 0) + 1
        data.setdefault("interactions", []).append({
            "timestamp": now,
            "chat_id": chat_id,
            "type": "reaction",
            "emoji": emoji,
        })
        
        data["interactions"] = data["interactions"][-20:]
        self._save(user_id)
    
    def remember_detail(self, user_id: str, key: str, value: str):
        """Запомнить деталь о пользователе (порода собаки, имя, etc.)."""
        data = self._load(user_id)
        data.setdefault("personal_details", {})[key] = value
        self._save(user_id)
    
    def add_note(self, user_id: str, note: str):
        """Добавить заметку о пользователе."""
        data = self._load(user_id)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        data.setdefault("notes", []).append(f"{timestamp}: {note}")
        data["notes"] = data["notes"][-20:]
        self._save(user_id)
    
    def update_funnel(self, user_id: str, stage: str):
        """Обновить стадию воронки."""
        data = self._load(user_id)
        data["funnel_stage"] = stage
        self._save(user_id)
    
    def record_my_message(self, user_id: str, chat_id: str):
        """Записать, что я отправил сообщение в чат (для anti-spam)."""
        data = self._load(user_id)
        now = int(time.time())
        data.setdefault("my_last_messages", {})[chat_id] = now
        self._save(user_id)
    
    def get_user_profile(self, user_id: str) -> UserMemoryProfile:
        """Получить профиль памяти пользователя."""
        data = self._load(user_id)
        
        return UserMemoryProfile(
            user_id=data.get("user_id", user_id),
            username=data.get("username", ""),
            display_name=data.get("display_name", ""),
            first_seen=data.get("first_seen", 0),
            last_seen=data.get("last_seen", 0),
            interaction_count=data.get("interaction_count", 0),
            source_chat_id=data.get("source_chat_id", ""),
            source_chat_title=data.get("source_chat_title", ""),
            funnel_stage=data.get("funnel_stage", "unknown"),
            topics_discussed=data.get("topics_discussed", []),
            personal_details=data.get("personal_details", {}),
            notes=data.get("notes", []),
            group_chats=data.get("group_chats", []),
            has_dm=data.get("has_dm", False),
            last_interaction_summary=data.get("last_interaction_summary", ""),
        )
    
    def get_user_context_for_prompt(self, user_id: str) -> str:
        """
        Получить контекст пользователя для промпта LLM.
        
        Returns:
            Строку с информацией о пользователе для инъекции в промпт.
        """
        profile = self.get_user_profile(user_id)
        
        if profile.interaction_count == 0:
            return "(первый контакт, ничего не знаем)"
        
        parts = []
        
        if profile.display_name:
            parts.append(f"Имя: {profile.display_name}")
        if profile.username:
            parts.append(f"@{profile.username}")
        
        # Interaction count
        parts.append(f"Взаимодействий: {profile.interaction_count}")
        
        # Personal details
        for key, value in profile.personal_details.items():
            parts.append(f"{key}: {value}")
        
        # Source chat (для DM)
        if profile.source_chat_title:
            parts.append(f"Пришёл из чата: {profile.source_chat_title}")
        
        # Topics
        if profile.topics_discussed:
            parts.append(f"Темы: {', '.join(profile.topics_discussed[-5:])}")
        
        # Funnel
        parts.append(f"Воронка: {profile.funnel_stage}")
        
        # Notes
        if profile.notes:
            parts.append(f"Заметки: {'; '.join(profile.notes[-3:])}")
        
        return "\n".join(parts)
    
    def get_last_interaction_summary(self, user_id: str) -> str:
        """Получить краткое описание последнего взаимодействия."""
        data = self._load(user_id)
        return data.get("last_interaction_summary", "")
    
    def get_all_users(self, funnel_stage: str = None) -> list[dict]:
        """Получить всех пользователей (опционально по стадии воронки)."""
        users = []
        
        if not os.path.exists(self.memory_dir):
            return users
        
        for filename in os.listdir(self.memory_dir):
            if not filename.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.memory_dir, filename), "r") as f:
                    data = json.load(f)
                if funnel_stage is None or data.get("funnel_stage") == funnel_stage:
                    users.append(data)
            except Exception:
                continue
        
        return users
