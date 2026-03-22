"""
Settings — все настройки из env vars с разумными defaults.

Все os.getenv() вызываются в default_factory — при создании экземпляра (lazy),
а не при импорте модуля. Это позволяет тестам патчить окружение до load_config().
"""

import os
from dataclasses import dataclass, field


@dataclass
class LLMConfig:
    """Настройки LLM моделей"""
    fast_model: str = field(default_factory=lambda: os.getenv("FAST_MODEL", "google/gemini-2.0-flash-001"))
    slow_model: str = field(default_factory=lambda: os.getenv("SLOW_MODEL", "anthropic/claude-3.5-sonnet"))
    api_key: str = field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", ""))
    api_base: str = field(default_factory=lambda: os.getenv("OPENROUTER_BASE", "https://openrouter.ai/api/v1"))
    max_tokens_fast: int = field(default_factory=lambda: int(os.getenv("MAX_TOKENS_FAST", "512")))
    max_tokens_slow: int = field(default_factory=lambda: int(os.getenv("MAX_TOKENS_SLOW", "2048")))
    temperature: float = field(default_factory=lambda: float(os.getenv("LLM_TEMPERATURE", "0.7")))
    timeout: int = field(default_factory=lambda: int(os.getenv("LLM_TIMEOUT", "30")))


@dataclass
class TelegramConfig:
    """Настройки Telegram"""
    bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    monitor_chats: list = field(default_factory=lambda: [
        c.strip() for c in os.getenv("MONITOR_CHATS", "").split(",") if c.strip()
    ])
    long_poll_timeout: int = field(default_factory=lambda: int(os.getenv("TG_POLL_TIMEOUT", "30")))
    allowed_updates: list = field(default_factory=lambda: ["message"])


@dataclass
class AntiSpamConfig:
    """Настройки anti-spam"""
    min_delay_sec: int = field(default_factory=lambda: int(os.getenv("ANTISPAM_MIN_DELAY", "5")))
    max_delay_sec: int = field(default_factory=lambda: int(os.getenv("ANTISPAM_MAX_DELAY", "30")))
    max_responses_per_hour: int = field(default_factory=lambda: int(os.getenv("MAX_RESPONSES_PER_HOUR", "10")))
    max_responses_per_chat_per_hour: int = field(default_factory=lambda: int(os.getenv("MAX_PER_CHAT_PER_HOUR", "3")))
    cooldown_after_response_sec: int = field(default_factory=lambda: int(os.getenv("COOLDOWN_SEC", "60")))


@dataclass
class MemoryConfig:
    """Настройки памяти"""
    memory_dir: str = field(default_factory=lambda: os.getenv("MEMORY_DIR", "data/memory"))
    max_group_messages: int = field(default_factory=lambda: int(os.getenv("MAX_GROUP_MSGS", "10")))
    forget_after_days: int = field(default_factory=lambda: int(os.getenv("FORGET_DAYS", "90")))
    log_dir: str = field(default_factory=lambda: os.getenv("LOG_DIR", "data/logs"))


@dataclass
class AppConfig:
    """Главный конфиг"""
    llm: LLMConfig = field(default_factory=LLMConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    antispam: AntiSpamConfig = field(default_factory=AntiSpamConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)

    contract_path: str = field(default_factory=lambda: os.getenv("CONTRACT_PATH", "contracts/korm/persona.yaml"))
    check_interval: int = field(default_factory=lambda: int(os.getenv("CHECK_INTERVAL", "60")))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    log_file: str = field(default_factory=lambda: os.getenv("LOG_FILE", "data/logs/sales_bot.log"))


def load_config() -> AppConfig:
    """Загрузить конфигурацию из env vars."""
    return AppConfig()
