"""
User Memory Store — персистентная память о пользователях.

Бэкенд: SQLite (WAL mode) вместо JSON-файлов.

Преимущества над JSON:
- get_recent_messages() — O(1) через индекс вместо O(n) сканирования всех файлов
- Нет race conditions при конкурентном доступе нескольких корутин
- Транзакции для атомарных обновлений
- Не разрастается в M файлов × N персон

Интерфейс полностью совместим с оригинальным JSON-хранилищем.
"""

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Callable, Optional

from ..utils.logger import get_logger

logger = get_logger("memory")


# ── Entity extractors ────────────────────────────────────────────────────────

def _extract_dog_info(data: dict, text: str):
    """Extract dog-related info (``memory.entity_profile: dog``)."""
    text_lower = text.lower()

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
        "джек рассел": "Джек Рассел",
        "шпиц": "Шпиц",
        "чихуахуа": "Чихуахуа",
        "пудель": "Пудель",
        "мопс": "Мопс",
        "алабай": "Алабай",
        "кавказец": "Кавказская овчарка",
        "стафф": "Стаффордширский терьер",
        "питбуль": "Питбуль",
        "боксёр": "Боксёр",
    }
    for keyword, breed in breeds.items():
        if keyword in text_lower and not data.get("dog_breed"):
            data["dog_breed"] = breed

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
        "зубной камень": "зубной камень",
        "запах изо рта": "запах изо рта",
        "вздутие": "вздутие живота",
    }
    for keyword, problem in problems.items():
        if keyword in text_lower and problem not in data.get("dog_problems", []):
            data.setdefault("dog_problems", []).append(problem)

    age_match = re.search(r"(\d+)\s*(год[а]?|лет|месяц[а]?)", text_lower)
    if age_match and not data.get("dog_age"):
        data["dog_age"] = age_match.group(0)

    name_match = re.search(r"(?:зовут|кличка|имя)\s+(\w+)", text_lower)
    if name_match and not data.get("dog_name"):
        data["dog_name"] = name_match.group(1).capitalize()


def _extract_fitness_info(data: dict, text: str):
    """Extract fitness-related info (for fitness persona)."""
    text_lower = text.lower()

    goals = {
        "похудеть": "снижение веса",
        "набрать массу": "набор массы",
        "рельеф": "рельеф",
        "выносливость": "выносливость",
        "гибкость": "гибкость",
        "сила": "сила",
    }
    for keyword, goal in goals.items():
        if keyword in text_lower and goal not in data.get("interests", []):
            data.setdefault("interests", []).append(goal)

    problems = {
        "колен": "проблемы с коленями",
        "спин": "проблемы со спиной",
        "плеч": "проблемы с плечами",
        "травм": "травма",
        "болит": "болевые ощущения",
    }
    for keyword, problem in problems.items():
        if keyword in text_lower and problem not in data.get("health_issues", []):
            data.setdefault("health_issues", []).append(problem)


def _extract_generic_info(data: dict, text: str):
    """Generic entity extraction."""
    text_lower = text.lower()

    topic_keywords = {
        "здоровь": "здоровье",
        "спорт": "спорт",
        "работ": "работа",
        "семь": "семья",
        "деньг": "финансы",
        "отпуск": "отдых",
    }
    for keyword, topic in topic_keywords.items():
        if keyword in text_lower and topic not in data.get("interests", []):
            data.setdefault("interests", []).append(topic)


def resolve_entity_extractor(entity_profile: str = "", persona_name: str = "") -> Callable:
    """Pick entity extractor from YAML ``memory.entity_profile`` (no persona-name tables in code).

    Args:
        entity_profile: ``dog`` | ``fitness`` | ``generic`` (or empty).
        persona_name: Unused; kept for API compatibility.

    Returns:
        Extractor callable ``(data: dict, text: str) -> None``.
    """
    _ = persona_name
    prof = (entity_profile or "").strip().lower()
    if prof == "dog":
        return _extract_dog_info
    if prof == "fitness":
        return _extract_fitness_info
    if prof in ("", "generic"):
        return _extract_generic_info
    logger.warning("Unknown memory.entity_profile %r, using generic extractor", entity_profile)
    return _extract_generic_info


# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    user_id          TEXT PRIMARY KEY,
    username         TEXT DEFAULT '',
    display_name     TEXT DEFAULT '',
    first_seen       TEXT NOT NULL,
    last_seen        TEXT NOT NULL,
    total_interactions INTEGER DEFAULT 0,
    has_dm           INTEGER DEFAULT 0,
    dm_history_summary TEXT DEFAULT '',
    funnel_stage     TEXT DEFAULT 'unknown',
    last_tool_name   TEXT,
    last_tool_args   TEXT DEFAULT '{}',
    notes            TEXT DEFAULT '[]',
    extra            TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS group_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    chat_id    TEXT NOT NULL,
    chat_title TEXT DEFAULT '',
    text       TEXT NOT NULL,
    ts         TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_gm_chat ON group_messages(chat_id, ts);
CREATE INDEX IF NOT EXISTS idx_gm_user ON group_messages(user_id, ts);

CREATE TABLE IF NOT EXISTS recommendations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    ts             TEXT NOT NULL,
    UNIQUE(user_id, recommendation),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
"""


class UserMemoryStore:
    """
    SQLite-backed memory store.

    Thread-safety: sqlite3 in WAL mode supports concurrent reads; writes are
    serialised per connection. Each persona uses its own DB file so there is
    no cross-persona contention.
    """

    def __init__(
        self,
        memory_dir: str = "data/memory",
        persona_name: str = "",
        entity_profile: str = "",
    ):
        self.memory_dir = memory_dir
        self.persona_name = (persona_name or "").lower()
        self.entity_profile = (entity_profile or "").strip().lower()
        os.makedirs(memory_dir, exist_ok=True)

        db_path = os.path.join(memory_dir, "memory.db")
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_DDL)
        self._conn.commit()

        self._extractor = resolve_entity_extractor(self.entity_profile, self.persona_name)
        self._db_lock = None  # reserved for future async serialization

    def _execute_with_retry(self, sql: str, parameters: tuple = None, max_retries: int = 3) -> sqlite3.Cursor:
        """Execute SQL with retry on database locked error."""
        for attempt in range(max_retries):
            try:
                if parameters:
                    return self._conn.execute(sql, parameters)
                return self._conn.execute(sql)
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < max_retries - 1:
                    import time
                    wait = 0.05 * (2 ** attempt)  # 50ms, 100ms, 200ms
                    logger.warning(f"Database locked, retrying in {wait}s (attempt {attempt + 1})")
                    time.sleep(wait)
                    continue
                raise

    def _extract_entities(self, data: dict, text: str):
        try:
            self._extractor(data, text)
        except Exception as e:
            logger.warning(f"Entity extraction error: {e}")

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _ensure_user(self, user_id: str, username: str = "", display_name: str = ""):
        """Upsert user row — idempotent."""
        now = self._now()
        self._execute_with_retry(
            """
            INSERT INTO users (user_id, username, display_name, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username     = COALESCE(NULLIF(excluded.username, ''), username),
                display_name = COALESCE(NULLIF(excluded.display_name, ''), display_name),
                last_seen    = excluded.last_seen
            """,
            (user_id, username, display_name, now, now),
        )

    def _load_extra(self, user_id: str) -> dict:
        cursor = self._execute_with_retry(
            "SELECT extra FROM users WHERE user_id = ?", (user_id,)
        )
        row = cursor.fetchone()
        if not row:
            return {}
        try:
            return json.loads(row["extra"] or "{}")
        except json.JSONDecodeError:
            return {}

    def _save_extra(self, user_id: str, extra: dict):
        self._conn.execute(
            "UPDATE users SET extra = ? WHERE user_id = ?",
            (json.dumps(extra, ensure_ascii=False), user_id),
        )

    def get_dm_inbound_streak(self, user_id: str) -> int:
        """Count of consecutive inbound DMs not yet answered by a bot send (see antispam)."""
        cursor = self._execute_with_retry(
            "SELECT extra FROM users WHERE user_id = ?", (user_id,)
        )
        row = cursor.fetchone()
        if not row:
            return 0
        try:
            extra = json.loads(row["extra"] or "{}")
        except json.JSONDecodeError:
            return 0
        try:
            return int(extra.get("dm_inbound_streak", 0))
        except (TypeError, ValueError):
            return 0

    def increment_dm_inbound_streak(self, user_id: str) -> int:
        """Increment streak after allowing this DM through antispam; returns new value."""
        with self._conn:
            self._ensure_user(user_id)
            extra = self._load_extra(user_id)
            n = int(extra.get("dm_inbound_streak", 0)) + 1
            extra["dm_inbound_streak"] = n
            self._save_extra(user_id, extra)
        return n

    def reset_dm_inbound_streak(self, user_id: str) -> None:
        """Clear streak after the bot successfully sent a DM reply."""
        with self._conn:
            cursor = self._execute_with_retry(
                "SELECT user_id FROM users WHERE user_id = ?", (user_id,)
            )
            if not cursor.fetchone():
                return
            extra = self._load_extra(user_id)
            if extra.get("dm_inbound_streak", 0) == 0:
                return
            extra["dm_inbound_streak"] = 0
            self._save_extra(user_id, extra)

    # ── Public write API ─────────────────────────────────────────────────────

    def record_group_message(
        self,
        user_id: str,
        username: str,
        display_name: str,
        chat_id: str,
        chat_title: str,
        message: str,
    ):
        """Record a group message and update user context."""
        now = self._now()
        with self._conn:
            self._ensure_user(user_id, username, display_name)
            self._conn.execute(
                "UPDATE users SET total_interactions = total_interactions + 1 WHERE user_id = ?",
                (user_id,),
            )
            self._conn.execute(
                "INSERT INTO group_messages (user_id, chat_id, chat_title, text, ts) VALUES (?,?,?,?,?)",
                (user_id, chat_id, chat_title, message[:500], now),
            )
            # Keep only last 10 group messages per user
            self._conn.execute(
                """
                DELETE FROM group_messages WHERE id IN (
                    SELECT id FROM group_messages WHERE user_id = ?
                    ORDER BY ts DESC LIMIT -1 OFFSET 10
                )
                """,
                (user_id,),
            )

            # Entity extraction
            extra = self._load_extra(user_id)
            self._extract_entities(extra, message)
            self._save_extra(user_id, extra)

    def record_dm(
        self,
        user_id: str,
        username: str,
        display_name: str,
        message: str,
        response: str,
        stage: str,
    ):
        """Record a DM exchange and advance the funnel."""
        with self._conn:
            self._ensure_user(user_id, username, display_name)

            funnel_update = {
                "soft_sell": "soft_sold",
                "direct_sell": "soft_sold",
                "help": "helping",
            }.get(stage)

            if funnel_update:
                self._conn.execute(
                    "UPDATE users SET funnel_stage = ? WHERE user_id = ?",
                    (funnel_update, user_id),
                )
            elif stage == "engage":
                self._conn.execute(
                    """
                    UPDATE users SET funnel_stage = 'engaged'
                    WHERE user_id = ? AND funnel_stage IN ('unknown', 'noticed')
                    """,
                    (user_id,),
                )

            self._conn.execute(
                """
                UPDATE users SET
                    has_dm = 1,
                    total_interactions = total_interactions + 1,
                    dm_history_summary = dm_history_summary || ?
                WHERE user_id = ?
                """,
                (f"\nUser: {message[:100]}\nBot: {response[:100]}\n", user_id),
            )

            extra = self._load_extra(user_id)
            self._extract_entities(extra, message)
            self._save_extra(user_id, extra)

    def add_note(self, user_id: str, note: str):
        """Append a note to the user's notes list."""
        with self._conn:
            self._ensure_user(user_id)
            row = self._conn.execute(
                "SELECT notes FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            notes: list[str] = json.loads(row["notes"] or "[]") if row else []
            notes.append(f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}: {note}")
            notes = notes[-20:]
            self._conn.execute(
                "UPDATE users SET notes = ? WHERE user_id = ?",
                (json.dumps(notes, ensure_ascii=False), user_id),
            )

    def record_recommendation(self, user_id: str, recommendation: str):
        """Record a product recommendation (ignores duplicates)."""
        with self._conn:
            self._ensure_user(user_id)
            self._conn.execute(
                "INSERT OR IGNORE INTO recommendations (user_id, recommendation, ts) VALUES (?,?,?)",
                (user_id, recommendation, self._now()),
            )

    # ── Public read API ──────────────────────────────────────────────────────

    def get_recommendations(self, user_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT recommendation FROM recommendations WHERE user_id = ? ORDER BY ts ASC LIMIT 10",
            (user_id,),
        ).fetchall()
        return [r["recommendation"] for r in rows]

    def get_user_context(self, user_id: str) -> str:
        """Build prompt-ready user context string."""
        row = self._conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            return ""

        parts = []
        if row["display_name"]:
            parts.append(f"Имя: {row['display_name']}")
        if row["username"]:
            parts.append(f"@{row['username']}")
        if row["total_interactions"] > 1:
            parts.append(f"Взаимодействий: {row['total_interactions']}")

        extra = json.loads(row["extra"] or "{}")
        if extra.get("dog_breed"):
            parts.append(f"Собака: {extra['dog_breed']}")
        if extra.get("dog_age"):
            parts.append(f"Возраст: {extra['dog_age']}")
        if extra.get("dog_name"):
            parts.append(f"Кличка: {extra['dog_name']}")
        if extra.get("dog_problems"):
            parts.append(f"Проблемы: {', '.join(extra['dog_problems'])}")
        if extra.get("interests"):
            parts.append(f"Интересы: {', '.join(extra['interests'][-5:])}")

        notes = json.loads(row["notes"] or "[]")
        if notes:
            parts.append(f"Заметки: {'; '.join(notes[-3:])}")

        recs = self.get_recommendations(user_id)
        if recs:
            parts.append(f"Уже рекомендовал: {'; '.join(recs[:3])}")

        parts.append(f"Воронка: {row['funnel_stage']}")
        return "\n".join(parts)

    def get_group_context_for_user(self, user_id: str) -> str:
        """Recent group messages from this user."""
        rows = self._conn.execute(
            """
            SELECT chat_title, text FROM group_messages
            WHERE user_id = ? ORDER BY ts DESC LIMIT 5
            """,
            (user_id,),
        ).fetchall()
        if not rows:
            return ""
        parts = ["Что писал в группе:"]
        for r in reversed(rows):
            parts.append(f"- [{r['chat_title'] or '?'}] {r['text'][:200]}")
        return "\n".join(parts)

    def get_recent_messages(self, chat_id: str, limit: int = 5) -> str:
        """
        Recent messages in a chat (from all users).
        O(1) via index — replaces the O(n) JSON file scan.
        """
        rows = self._conn.execute(
            """
            SELECT gm.text
            FROM group_messages gm
            WHERE gm.chat_id = ?
            ORDER BY gm.ts DESC LIMIT ?
            """,
            (chat_id, limit),
        ).fetchall()
        if not rows:
            return ""
        parts = ["Последние сообщения в чате:"]
        for r in reversed(rows):
            parts.append(f"- {r['text'][:150]}")
        return "\n".join(parts)

    def get_funnel_stage(self, user_id: str) -> str:
        row = self._conn.execute(
            "SELECT funnel_stage FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["funnel_stage"] if row else "unknown"

    def analyze_funnel_signals(self, user_id: str, message: str) -> str:
        """Detect funnel progression signals in *message*."""
        from ..core.funnel_heuristic import suggest_funnel_stage

        return suggest_funnel_stage(self.get_funnel_stage(user_id), message)

    def get_all_users(self, stage: Optional[str] = None) -> list[dict]:
        """Return all users, optionally filtered by funnel stage."""
        if stage:
            rows = self._conn.execute(
                "SELECT * FROM users WHERE funnel_stage = ?", (stage,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM users").fetchall()
        return [dict(r) for r in rows]

    def get_last_tool(self, user_id: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT last_tool_name FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["last_tool_name"] if row else None

    def get_last_tool_args(self, user_id: str) -> dict:
        row = self._conn.execute(
            "SELECT last_tool_args FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            return {}
        try:
            return json.loads(row["last_tool_args"] or "{}")
        except json.JSONDecodeError:
            return {}

    def set_last_tool(self, user_id: str, tool_name: str, tool_args: dict = None):
        with self._conn:
            self._ensure_user(user_id)
            self._conn.execute(
                "UPDATE users SET last_tool_name = ?, last_tool_args = ? WHERE user_id = ?",
                (tool_name, json.dumps(tool_args or {}, ensure_ascii=False), user_id),
            )

    def is_first_response(self, user_id: str, chat_id: str) -> bool:
        """True if this user has no recorded messages in any chat."""
        count = self._conn.execute(
            "SELECT COUNT(*) AS c FROM group_messages WHERE user_id = ?", (user_id,)
        ).fetchone()["c"]
        has_dm = self._conn.execute(
            "SELECT has_dm FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        dm_flag = bool(has_dm["has_dm"]) if has_dm else False
        return count == 0 and not dm_flag

    def close(self):
        """Close the database connection."""
        try:
            self._conn.close()
        except Exception:
            pass

    # ── Backward-compat shim ─────────────────────────────────────────────────

    def _load(self, user_id: str) -> dict:
        """
        Return a dict snapshot of the user's state — mirrors the old JSON format.

        Used by tests that inspect internal state directly. New production code
        should use the public API methods instead of _load().
        """
        self._ensure_user(user_id)
        row = self._conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()

        extra = json.loads(row["extra"] or "{}")
        notes = json.loads(row["notes"] or "[]")

        gm_rows = self._conn.execute(
            "SELECT chat_id, chat_title, text, ts FROM group_messages WHERE user_id = ? ORDER BY ts",
            (user_id,),
        ).fetchall()
        group_messages = [
            {"chat_id": r["chat_id"], "chat_title": r["chat_title"], "text": r["text"], "timestamp": r["ts"]}
            for r in gm_rows
        ]

        return {
            "user_id": row["user_id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
            "total_interactions": row["total_interactions"],
            "has_dm": bool(row["has_dm"]),
            "dm_history_summary": row["dm_history_summary"],
            "funnel_stage": row["funnel_stage"],
            "last_tool_name": row["last_tool_name"],
            "last_tool_args": json.loads(row["last_tool_args"] or "{}"),
            "notes": notes,
            # Entity-extracted fields (flat, for test compatibility)
            "dog_breed": extra.get("dog_breed"),
            "dog_age": extra.get("dog_age"),
            "dog_name": extra.get("dog_name"),
            "dog_problems": extra.get("dog_problems", []),
            "interests": extra.get("interests", []),
            "health_issues": extra.get("health_issues", []),
            # Joined from separate table
            "group_messages": group_messages,
            "group_chats": list({r["chat_id"] for r in gm_rows}),
            "recommendations": self.get_recommendations(user_id),
        }
