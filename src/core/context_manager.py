"""
Context Manager — namespace-based контекст с TTL.

Скопировано и адаптировано из ai-tutor-engine/src/agents/core/context_manager.py

Управляет контекстом персонажей с namespace-изоляцией:
- {"ns": {"persona_name": {key: value}}}
- Whitelist allowed keys (keep_keys)
- TTL-based expiration
- Предотвращает cross-domain contamination
"""

import logging
import time
from typing import Any, Optional

from .vibe_schema import ContextPolicy

logger = logging.getLogger("context-manager")

try:
    from ..utils.logger import get_logger
    logger = get_logger("context-manager")
except Exception:
    pass


class ContextManager:
    """
    Управляет namespace-based контекстом для персонажей.
    
    Формат контекста:
    {
        "ns": {
            "persona_name": {
                "key1": {"value": ..., "timestamp": ...},
                "key2": {"value": ..., "timestamp": ...},
            }
        }
    }
    
    Предотвращает cross-domain contamination (контекст одного persona
    не должен влиять на другой).
    """
    
    def __init__(
        self,
        namespace: str,
        keep_keys: Optional[list[str]] = None,
        ttl_turns: int = 10,
    ):
        """
        Args:
            namespace: Имя namespace (обычно имя персонажа)
            keep_keys: Whitelist ключей (если пусто — все ключи разрешены)
            ttl_turns: TTL в количестве ходов (обновлений)
        """
        self.namespace = namespace
        self.keep_keys = keep_keys or []
        self.ttl_turns = ttl_turns
        self._turn_count = 0
        self._context: dict[str, Any] = {"ns": {}}
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Получить значение из контекста namespace.
        
        Args:
            key: Ключ
            default: Значение по умолчанию
        
        Returns:
            Значение или default
        """
        ns_data = self._context.get("ns", {}).get(self.namespace, {})
        entry = ns_data.get(key)
        
        if entry is None:
            return default
        
        # Проверяем TTL
        if isinstance(entry, dict) and "timestamp" in entry:
            if self._is_expired(entry):
                self.delete(key)
                return default
            return entry.get("value", default)
        
        return entry
    
    def set(self, key: str, value: Any):
        """
        Установить значение в контекст namespace.
        
        Args:
            key: Ключ
            value: Значение
        """
        # Проверяем whitelist
        if self.keep_keys and key not in self.keep_keys:
            logger.debug(f"ContextManager: Key '{key}' not in keep_keys, skipping")
            return
        
        if "ns" not in self._context:
            self._context["ns"] = {}
        if self.namespace not in self._context["ns"]:
            self._context["ns"][self.namespace] = {}
        
        self._context["ns"][self.namespace][key] = {
            "value": value,
            "timestamp": self._now(),
            "turn": self._turn_count,
        }
    
    def delete(self, key: str):
        """Удалить ключ из контекста."""
        ns_data = self._context.get("ns", {}).get(self.namespace, {})
        ns_data.pop(key, None)
    
    def update(self, updates: dict[str, Any]):
        """
        Массовое обновление контекста.
        
        Args:
            updates: Словарь {key: value}
        """
        for key, value in updates.items():
            self.set(key, value)
    
    def increment_turn(self):
        """Увеличить счётчик ходов и очистить истёкшие записи."""
        self._turn_count += 1
        self._cleanup_expired()
    
    def get_all(self) -> dict[str, Any]:
        """Получить все данные namespace."""
        ns_data = self._context.get("ns", {}).get(self.namespace, {})
        result = {}
        for key, entry in ns_data.items():
            if isinstance(entry, dict) and "value" in entry:
                if not self._is_expired(entry):
                    result[key] = entry["value"]
            else:
                result[key] = entry
        return result
    
    def get_state_context(self) -> dict[str, Any]:
        """Получить полный state context (для передачи между компонентами)."""
        return self._context.copy()
    
    def load_state_context(self, state_context: dict[str, Any]):
        """Загрузить state context (восстановление извне)."""
        if state_context and isinstance(state_context, dict):
            self._context = state_context.copy()
    
    def clear(self):
        """Очистить контекст namespace."""
        if "ns" in self._context and self.namespace in self._context["ns"]:
            self._context["ns"][self.namespace] = {}
    
    def _is_expired(self, entry: dict) -> bool:
        """Проверить, истекла ли запись по TTL."""
        if not isinstance(entry, dict):
            return False
        
        entry_turn = entry.get("turn", 0)
        return (self._turn_count - entry_turn) > self.ttl_turns
    
    def _cleanup_expired(self):
        """Удалить все истёкшие записи."""
        ns_data = self._context.get("ns", {}).get(self.namespace, {})
        expired_keys = []
        
        for key, entry in ns_data.items():
            if isinstance(entry, dict) and self._is_expired(entry):
                expired_keys.append(key)
        
        for key in expired_keys:
            del ns_data[key]
            logger.debug(f"ContextManager: Expired key '{key}'")
    
    @staticmethod
    def _now() -> int:
        return int(time.time())
    
    @staticmethod
    def from_policy(namespace: str, policy: Optional[ContextPolicy]) -> "ContextManager":
        """Создать ContextManager из ContextPolicy."""
        if policy:
            return ContextManager(
                namespace=policy.namespace or namespace,
                keep_keys=policy.keep_keys,
                ttl_turns=policy.ttl_turns,
            )
        return ContextManager(namespace=namespace)
