"""
User Memory Store — персистентная память о пользователях
JSON файлы, один на юзера. Переживает рестарт.
"""

import json
import os
import re
from datetime import datetime
from typing import Optional

from ..utils.logger import get_logger

logger = get_logger("memory")


class UserMemoryStore:
    """Хранилище памяти о пользователях"""
    
    def __init__(self, memory_dir: str = "data/memory"):
        self.memory_dir = memory_dir
        os.makedirs(memory_dir, exist_ok=True)
        self._cache: dict = {}  # user_id -> data
    
    def _path(self, user_id: str) -> str:
        return os.path.join(self.memory_dir, f"{user_id}.json")
    
    def _load(self, user_id: str) -> dict:
        """Загрузить данные юзера"""
        if user_id in self._cache:
            return self._cache[user_id]
        
        path = self._path(user_id)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._cache[user_id] = data
                return data
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"Failed to load memory for {user_id}: {e}")
        
        # Создаём нового
        data = {
            "user_id": user_id,
            "username": "",
            "display_name": "",
            "dog_breed": None,
            "dog_age": None,
            "dog_name": None,
            "dog_problems": [],
            "first_seen": datetime.utcnow().isoformat() + "Z",
            "last_seen": datetime.utcnow().isoformat() + "Z",
            "total_interactions": 0,
            "group_chats": [],
            "group_messages": [],
            "has_dm": False,
            "dm_history_summary": "",
            "funnel_stage": "unknown",
            "products_mentioned": [],
            "notes": [],
        }
        self._cache[user_id] = data
        return data
    
    def _save(self, user_id: str):
        """Сохранить данные юзера"""
        path = self._path(user_id)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._cache[user_id], f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save memory for {user_id}: {e}")
    
    def record_group_message(
        self,
        user_id: str,
        username: str,
        display_name: str,
        chat_id: str,
        chat_title: str,
        message: str,
    ):
        """Записать сообщение из группы"""
        data = self._load(user_id)
        
        data["username"] = username or data.get("username", "")
        data["display_name"] = display_name or data.get("display_name", "")
        data["last_seen"] = datetime.utcnow().isoformat() + "Z"
        data["total_interactions"] = data.get("total_interactions", 0) + 1
        
        if chat_id not in data.get("group_chats", []):
            data.setdefault("group_chats", []).append(chat_id)
        
        # Сохраняем последние N сообщений
        data.setdefault("group_messages", []).append({
            "chat_id": chat_id,
            "chat_title": chat_title,
            "text": message[:500],
            "timestamp": datetime.utcnow().isoformat() + "Z",
        })
        # Обрезаем до 10
        data["group_messages"] = data["group_messages"][-10:]
        
        # Авто-извлечение информации о собаке
        self._extract_dog_info(data, message)
        
        self._save(user_id)
    
    def record_dm(
        self,
        user_id: str,
        username: str,
        display_name: str,
        message: str,
        response: str,
        stage: str,
    ):
        """Записать ЛС обмен"""
        data = self._load(user_id)
        
        data["username"] = username or data.get("username", "")
        data["display_name"] = display_name or data.get("display_name", "")
        data["last_seen"] = datetime.utcnow().isoformat() + "Z"
        data["has_dm"] = True
        data["total_interactions"] = data.get("total_interactions", 0) + 1
        
        # Обновляем воронку
        if stage in ("soft_sell", "direct_sell"):
            data["funnel_stage"] = "soft_sold"
        elif stage == "help":
            data["funnel_stage"] = "helping"
        elif stage == "engage":
            if data.get("funnel_stage") in ("unknown", "noticed"):
                data["funnel_stage"] = "engaged"
        
        # Обновляем summary
        summary = data.get("dm_history_summary", "")
        data["dm_history_summary"] = summary + f"\nUser: {message[:100]}\nBot: {response[:100]}\n"
        
        self._extract_dog_info(data, message)
        self._save(user_id)
    
    def add_note(self, user_id: str, note: str):
        """Добавить заметку"""
        data = self._load(user_id)
        data.setdefault("notes", []).append(
            f"{datetime.utcnow().strftime('%Y-%m-%d')}: {note}"
        )
        data["notes"] = data["notes"][-20:]  # максимум 20 заметок
        self._save(user_id)
    
    def get_user_context(self, user_id: str) -> str:
        """Получить контекст юзера для промпта"""
        data = self._load(user_id)
        
        parts = []
        
        if data.get("display_name"):
            parts.append(f"Имя: {data['display_name']}")
        if data.get("username"):
            parts.append(f"@{data['username']}")
        
        if data.get("dog_breed"):
            parts.append(f"Собака: {data['dog_breed']}")
        if data.get("dog_age"):
            parts.append(f"Возраст: {data['dog_age']}")
        if data.get("dog_name"):
            parts.append(f"Кличка: {data['dog_name']}")
        if data.get("dog_problems"):
            parts.append(f"Проблемы: {', '.join(data['dog_problems'])}")
        
        if data.get("notes"):
            parts.append(f"Заметки: {'; '.join(data['notes'][-3:])}")
        
        parts.append(f"Воронка: {data.get('funnel_stage', 'unknown')}")
        
        return "\n".join(parts) if parts else ""
    
    def get_group_context_for_user(self, user_id: str) -> str:
        """Получить контекст из групповых сообщений"""
        data = self._load(user_id)
        messages = data.get("group_messages", [])
        
        if not messages:
            return ""
        
        parts = ["Что писал в группе:"]
        for msg in messages[-5:]:
            parts.append(f"- [{msg.get('chat_title', '?')}] {msg['text'][:200]}")
        
        return "\n".join(parts)
    
    def get_recent_messages(self, chat_id: str, limit: int = 5) -> str:
        """Получить последние сообщения из чата (из всех юзеров)"""
        # Простая реализация — читаем все файлы
        # TODO: оптимизировать через индекс
        all_messages = []
        
        if not os.path.exists(self.memory_dir):
            return ""
        
        for filename in os.listdir(self.memory_dir):
            if not filename.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.memory_dir, filename), "r") as f:
                    data = json.load(f)
                for msg in data.get("group_messages", []):
                    if msg.get("chat_id") == chat_id:
                        all_messages.append(msg)
            except Exception:
                continue
        
        # Сортируем по времени, берём последние
        all_messages.sort(key=lambda m: m.get("timestamp", ""))
        recent = all_messages[-limit:]
        
        if not recent:
            return ""
        
        parts = [f"Последние сообщения в чате:"]
        for msg in recent:
            parts.append(f"- {msg['text'][:150]}")
        
        return "\n".join(parts)
    
    def get_funnel_stage(self, user_id: str) -> str:
        """Получить стадию воронки"""
        data = self._load(user_id)
        return data.get("funnel_stage", "unknown")
    
    def _extract_dog_info(self, data: dict, text: str):
        """Авто-извлечение информации о собаке"""
        text_lower = text.lower()
        
        # Породы
        breeds = {
            "овчарка": "Немецкая овчарка",
            "малинуа": "Бельгийская овчарка (Малинуа)",
            "лабрадор": "Лабрадор",
            "хаски": "Хаски",
            "ротвейлер": "Ротвейлер",
            "корги": "Корги",
            "такса": "Такса",
            "бигль": "Бигль",
            "спаниэль": "Спаниэль",
            "доберман": "Доберман",
        }
        for keyword, breed in breeds.items():
            if keyword in text_lower and not data.get("dog_breed"):
                data["dog_breed"] = breed
        
        # Проблемы
        problems = {
            "аллергия": "аллергия",
            "чешется": "зуд/чесотка",
            "понос": "проблемы с ЖКТ",
            "рвота": "рвота",
            "похудел": "потеря веса",
            "отказывается есть": "отказ от еды",
            "хромает": "хромота",
            "красные уши": "воспаление ушей",
            "слезятся глаза": "слезоточивость",
            "выпадает шерсть": "выпадение шерсти",
        }
        for keyword, problem in problems.items():
            if keyword in text_lower and problem not in data.get("dog_problems", []):
                data.setdefault("dog_problems", []).append(problem)
        
        # Возраст
        age_match = re.search(r'(\d+)\s*(год[а]?|лет|месяц[а]?)', text_lower)
        if age_match and not data.get("dog_age"):
            data["dog_age"] = age_match.group(0)
        
        # Кличка (простой паттерн: "зовут X", "кличка X")
        name_match = re.search(r'(?:зовут|кличка|имя)\s+(\w+)', text_lower)
        if name_match and not data.get("dog_name"):
            data["dog_name"] = name_match.group(1).capitalize()
    
    def get_all_users(self, stage: str = None) -> list[dict]:
        """Получить всех юзеров (опционально фильтр по воронке)"""
        users = []
        
        if not os.path.exists(self.memory_dir):
            return users
        
        for filename in os.listdir(self.memory_dir):
            if not filename.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.memory_dir, filename), "r") as f:
                    data = json.load(f)
                if stage is None or data.get("funnel_stage") == stage:
                    users.append(data)
            except Exception:
                continue
        
        return users
