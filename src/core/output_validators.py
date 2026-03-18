"""
Output Validators — проверка сгенерированного ответа на запрещённые фразы.

Скопировано из ai-tutor-engine паттерна OutputValidators.

Детерминистическая проверка:
- banned_phrases — список запрещённых фраз (маркетинговый жаргон, кросс-контаминация)
- greeting_policy — когда здороваться, когда нет
- Форматирование проверки
"""

import re
import logging
from dataclasses import dataclass
from typing import Optional

from .vibe_schema import OutputValidators as OutputValidatorsConfig, GreetingPolicy

logger = logging.getLogger("output-validators")

try:
    from ..utils.logger import get_logger
    logger = get_logger("output-validators")
except Exception:
    pass


@dataclass
class ValidationResult:
    """Результат валидации ответа."""
    is_valid: bool
    cleaned_text: str
    violations: list[str]
    greeting_stripped: bool = False


class OutputValidator:
    """
    Валидатор ответов — проверяет сгенерированный текст.
    
    Проверки:
    1. banned_phrases — запрещённые фразы (маркетинг, cross-contamination)
    2. greeting_policy — убирать приветствие если не положено
    3. format checks — длина, форматирование
    """
    
    # Приветствия (regex паттерны)
    GREETING_PATTERNS = [
        r"^(привет[!,.\s]*)",
        r"^(здравствуйте[!,.\s]*)",
        r"^(здрасьте[!,.\s]*)",
        r"^(здарова[!,.\s]*)",
        r"^(йо[!,.\s]*)",
        r"^(хай[!,.\s]*)",
        r"^(добрый день[!,.\s]*)",
        r"^(доброе утро[!,.\s]*)",
        r"^(добрый вечер[!,.\s]*)",
        r"^(хей[!,.\s]*)",
        r"^(hello[!,.\s]*)",
        r"^(hi[!,.\s]*)",
    ]
    
    def __init__(
        self,
        validators_config: Optional[OutputValidatorsConfig] = None,
        greeting_policy: Optional[GreetingPolicy] = None,
    ):
        """
        Args:
            validators_config: Конфигурация banned_phrases
            greeting_policy: Политика приветствий
        """
        self.banned_phrases = []
        self.greeting_policy = greeting_policy
        
        if validators_config:
            self.banned_phrases = [
                phrase.lower() for phrase in validators_config.banned_phrases
            ]
    
    def validate(
        self,
        text: str,
        is_first_response: bool = False,
        user_greeted: bool = False,
    ) -> ValidationResult:
        """
        Проверить ответ.
        
        Args:
            text: Сгенерированный текст
            is_first_response: Первый ответ этому пользователю?
            user_greeted: Пользователь поприветствовал?
        
        Returns:
            ValidationResult
        """
        violations = []
        cleaned = text
        greeting_stripped = False
        
        # === 1. Check banned phrases ===
        banned_result = self._check_banned_phrases(cleaned)
        violations.extend(banned_result["violations"])
        
        # === 2. Check greeting policy ===
        should_strip = self._should_strip_greeting(is_first_response, user_greeted)
        if should_strip:
            stripped = self._strip_greeting(cleaned)
            if stripped != cleaned:
                cleaned = stripped
                greeting_stripped = True
        
        # === 3. Check format ===
        format_violations = self._check_format(cleaned)
        violations.extend(format_violations)
        
        is_valid = len(violations) == 0
        
        return ValidationResult(
            is_valid=is_valid,
            cleaned_text=cleaned,
            violations=violations,
            greeting_stripped=greeting_stripped,
        )
    
    def _check_banned_phrases(self, text: str) -> dict:
        """Проверка banned phrases."""
        violations = []
        text_lower = text.lower()
        
        for phrase in self.banned_phrases:
            if phrase in text_lower:
                violations.append(f"Banned phrase: '{phrase}'")
                logger.warning(f"Banned phrase detected: '{phrase}'")
        
        return {"violations": violations}
    
    def _should_strip_greeting(
        self,
        is_first_response: bool,
        user_greeted: bool,
    ) -> bool:
        """Нужно ли убирать приветствие?"""
        if not self.greeting_policy or not self.greeting_policy.enabled:
            return False
        
        gp = self.greeting_policy
        
        # Если только первое сообщение — не первое → убираем
        if gp.greet_only_first_response and not is_first_response:
            return True
        
        # Если только если пользователь поприветствовал — не поприветствовал → убираем
        if gp.greet_only_if_user_greeted and not user_greeted:
            return True
        
        return False
    
    def _strip_greeting(self, text: str) -> str:
        """Убрать приветствие из начала текста."""
        cleaned = text
        
        for pattern in self.GREETING_PATTERNS:
            cleaned = re.sub(pattern, "", cleaned, count=1, flags=re.IGNORECASE)
        
        # Убрать лишние пробелы/пунктуацию в начале
        cleaned = cleaned.lstrip(" ,.!;:-")
        
        # Сделать первую букву заглавной
        if cleaned:
            cleaned = cleaned[0].upper() + cleaned[1:]
        
        return cleaned.strip()
    
    def _check_format(self, text: str) -> list[str]:
        """Проверка формата."""
        violations = []
        
        # Слишком длинный
        if len(text) > 2000:
            violations.append("Response too long (>2000 chars)")
        
        # Пустой
        if not text.strip():
            violations.append("Empty response")
        
        # Только эмодзи
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"
            "\U0001F300-\U0001F5FF"
            "\U0001F680-\U0001F6FF"
            "\U0001F1E0-\U0001F1FF"
            "\U00002702-\U000027B0"
            "\U0001F900-\U0001F9FF"
            "\U0001FA00-\U0001FA6F"
            "\U0001FA70-\U0001FAFF"
            "\U00002600-\U000026FF"
            "\U0000200D"
            "\U0000FE0F"
            "\U00002764"
            "\U0001FAE8"
            "]+", flags=re.UNICODE
        )
        text_no_emoji = emoji_pattern.sub('', text).strip()
        if not text_no_emoji and text.strip():
            violations.append("Emoji-only response")
        
        return violations
    
    def get_fallback_greeting(self) -> str:
        """Получить fallback приветствие из политики."""
        if self.greeting_policy and self.greeting_policy.fallback_variants:
            import random
            return random.choice(self.greeting_policy.fallback_variants)
        return ""
