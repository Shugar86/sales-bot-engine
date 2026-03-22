"""
Message Deduplication — SQLite-backed with atomic writes and concurrent safety.

Tracks processed messages to avoid duplicate responses.
Also tracks chat activity and recent responses for anti-spam.
"""

import asyncio
import hashlib
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .logger import get_logger

logger = get_logger("dedup")


@dataclass
class DeduplicationStore:
    """
    SQLite-backed deduplication store.

    Features:
    - Atomic writes (no corruption on crash)
    - Concurrent-safe (proper locking)
    - Automatic cleanup of old entries
    - Per-chat response tracking
    """

    storage_path: str = "data/memory/processed_messages.json"  # Legacy name, now uses SQLite
    max_age_hours: int = 48  # Keep records for 48 hours

    # Internal state (kept for backward compat)
    _processed: dict = None
    _chat_activity: Dict[str, float] = field(default_factory=dict)
    _recent_responses: Dict[str, List[str]] = field(default_factory=lambda: defaultdict(list))

    # SQLite connection (lazy init)
    _db_path: str = None
    _connection: Optional[sqlite3.Connection] = None
    _write_lock: asyncio.Lock = None

    def __post_init__(self):
        # Convert storage path to DB path (same dir, .db extension)
        storage = Path(self.storage_path)
        self._db_path = str(storage.parent / (storage.stem + ".db"))

        # Initialize async lock for concurrent safety
        self._write_lock = asyncio.Lock()

        # Initialize SQLite tables
        self._init_db()
        self._cleanup_old()

        # Load chat activity and recent responses into memory (lightweight)
        self._load_chat_activity()

    def _init_db(self):
        """Initialize SQLite database with WAL mode for concurrent reads."""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")

        # Create tables
        conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_messages (
                message_hash TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                text_preview TEXT,
                processed_at REAL NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                response_hash TEXT NOT NULL,
                response_preview TEXT,
                responded_at REAL NOT NULL
            )
        """)

        # Create indexes
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_processed_chat
            ON processed_messages(chat_id, processed_at)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_responses_chat
            ON bot_responses(chat_id, responded_at)
        """)

        conn.commit()
        conn.close()

    def _get_connection(self) -> sqlite3.Connection:
        """Get SQLite connection (with retry on locked DB)."""
        if self._connection is None:
            for attempt in range(3):
                try:
                    self._connection = sqlite3.connect(self._db_path, check_same_thread=False)
                    self._connection.execute("PRAGMA journal_mode=WAL")
                    return self._connection
                except sqlite3.OperationalError as e:
                    if "locked" in str(e).lower() and attempt < 2:
                        time.sleep(0.1 * (attempt + 1))
                        continue
                    raise
        return self._connection

    def _hash_message(self, chat_id: str, message_id: int, text: str) -> str:
        """Compute message hash."""
        content = f"{chat_id}:{message_id}:{text[:100]}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def is_processed(self, chat_id: str, message_id: int, text: str) -> bool:
        """Check if message has been processed."""
        h = self._hash_message(chat_id, message_id, text)

        try:
            conn = self._get_connection()
            cursor = conn.execute(
                "SELECT 1 FROM processed_messages WHERE message_hash = ?",
                (h,)
            )
            return cursor.fetchone() is not None
        except sqlite3.Error as e:
            logger.error(f"Database error in is_processed: {e}")
            # Fail safe: assume processed to avoid duplicate
            return True

    async def mark_processed(self, chat_id: str, message_id: int, text: str):
        """Mark message as processed."""
        h = self._hash_message(chat_id, message_id, text)

        async with self._write_lock:
            try:
                conn = self._get_connection()
                conn.execute(
                    """
                    INSERT OR REPLACE INTO processed_messages
                    (message_hash, chat_id, message_id, text_preview, processed_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (h, chat_id, message_id, text[:100], time.time())
                )
                conn.commit()
            except sqlite3.Error as e:
                logger.error(f"Database error in mark_processed: {e}")

    async def record_bot_response(self, chat_id: str, response_text: str = ""):
        """Record that the bot responded in a chat."""
        now = time.time()
        self._chat_activity[chat_id] = now

        # Store in SQLite
        if response_text:
            response_hash = hashlib.sha256(response_text.encode()).hexdigest()[:16]
            async with self._write_lock:
                try:
                    conn = self._get_connection()
                    conn.execute(
                        """
                        INSERT INTO bot_responses
                        (chat_id, response_hash, response_preview, responded_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (chat_id, response_hash, response_text[:200], now)
                    )
                    conn.commit()

                    # Cleanup old responses (keep last 10 per chat)
                    conn.execute(
                        """
                        DELETE FROM bot_responses
                        WHERE id IN (
                            SELECT id FROM bot_responses
                            WHERE chat_id = ?
                            ORDER BY responded_at DESC
                            LIMIT -1 OFFSET 10
                        )
                        """,
                        (chat_id,)
                    )
                    conn.commit()
                except sqlite3.Error as e:
                    logger.error(f"Database error in record_bot_response: {e}")

        # Update in-memory cache
        if response_text:
            self._recent_responses[chat_id].append(response_text[:200])
            self._recent_responses[chat_id] = self._recent_responses[chat_id][-10:]

    def last_bot_response_time(self, chat_id: str) -> Optional[float]:
        """When did the bot last respond in this chat?"""
        return self._chat_activity.get(chat_id)

    def seconds_since_last_response(self, chat_id: str) -> Optional[float]:
        """How many seconds since bot's last response in this chat."""
        last = self._chat_activity.get(chat_id)
        if last is None:
            return None
        return time.time() - last

    def is_repeating_response(self, chat_id: str, new_response: str, similarity_threshold: float = 0.8) -> bool:
        """Check if this response is too similar to recent ones."""
        # Use in-memory cache for speed
        recent = self._recent_responses.get(chat_id, [])
        if not recent:
            return False

        new_lower = new_response.lower().strip()
        new_words = set(new_lower.split())

        for old_response in recent:
            old_lower = old_response.lower().strip()
            if new_lower == old_lower:
                return True

            # Word overlap similarity
            old_words = set(old_lower.split())
            if new_words and old_words:
                overlap = len(new_words & old_words) / max(len(new_words | old_words), 1)
                if overlap > similarity_threshold:
                    return True

        return False

    def get_recent_texts(self, chat_id: str, limit: int = 10) -> list[str]:
        """Get recent response texts for a chat."""
        try:
            conn = self._get_connection()
            cursor = conn.execute(
                """
                SELECT response_preview FROM bot_responses
                WHERE chat_id = ?
                ORDER BY responded_at DESC
                LIMIT ?
                """,
                (chat_id, limit)
            )
            rows = cursor.fetchall()
            return [row[0] for row in rows]
        except sqlite3.Error as e:
            logger.error(f"Database error in get_recent_texts: {e}")
            return []

    def get_stats(self) -> dict:
        """Statistics."""
        try:
            conn = self._get_connection()

            # Count processed messages
            cursor = conn.execute("SELECT COUNT(*) FROM processed_messages")
            total_processed = cursor.fetchone()[0]

            # Count unique chats
            cursor = conn.execute("SELECT COUNT(DISTINCT chat_id) FROM bot_responses")
            chats_with_responses = cursor.fetchone()[0]

            return {
                "total_tracked": total_processed,
                "chats_active": len(self._chat_activity),
                "chats_with_responses": chats_with_responses,
                "storage": self._db_path,
            }
        except sqlite3.Error as e:
            logger.error(f"Database error in get_stats: {e}")
            return {
                "total_tracked": 0,
                "chats_active": 0,
                "chats_with_responses": 0,
                "storage": self._db_path,
                "error": str(e),
            }

    def _load_chat_activity(self):
        """Load chat activity from database into memory."""
        try:
            conn = self._get_connection()

            # Get latest response per chat
            cursor = conn.execute(
                """
                SELECT chat_id, MAX(responded_at)
                FROM bot_responses
                GROUP BY chat_id
                """
            )
            for row in cursor.fetchall():
                self._chat_activity[row[0]] = row[1]

            # Get recent responses per chat (last 10)
            cursor = conn.execute(
                """
                SELECT chat_id, response_preview
                FROM bot_responses
                ORDER BY responded_at DESC
                LIMIT 1000
                """
            )
            for row in cursor.fetchall():
                chat_id = row[0]
                if len(self._recent_responses[chat_id]) < 10:
                    self._recent_responses[chat_id].append(row[1])

            # Reverse to get chronological order
            for chat_id in self._recent_responses:
                self._recent_responses[chat_id].reverse()

        except sqlite3.Error as e:
            logger.error(f"Database error in _load_chat_activity: {e}")

    def _cleanup_old(self):
        """Delete old entries older than max_age_hours."""
        cutoff = time.time() - (self.max_age_hours * 3600)

        try:
            conn = self._get_connection()
            cursor = conn.execute(
                "DELETE FROM processed_messages WHERE processed_at < ?",
                (cutoff,)
            )
            deleted_processed = cursor.rowcount

            cursor = conn.execute(
                "DELETE FROM bot_responses WHERE responded_at < ?",
                (cutoff,)
            )
            deleted_responses = cursor.rowcount

            conn.commit()

            if deleted_processed > 0 or deleted_responses > 0:
                logger.debug(f"Cleaned up {deleted_processed} processed messages, {deleted_responses} responses")
        except sqlite3.Error as e:
            logger.error(f"Database error in _cleanup_old: {e}")

    def close(self):
        """Close database connection."""
        if self._connection:
            try:
                self._connection.close()
            except sqlite3.Error:
                pass
            finally:
                self._connection = None

    def __del__(self):
        """Cleanup on destruction."""
        self.close()
