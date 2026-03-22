"""
Structured logging setup
"""

import json
import logging
import logging.handlers
import os
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """JSON-формат для structured logging"""
    
    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # Добавляем exception info если есть
        if record.exc_info and record.exc_info[0]:
            log_obj["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
            }
        
        # Добавляем extra fields
        for key, value in record.__dict__.items():
            if key not in (
                "name", "msg", "args", "created", "filename", "funcName",
                "levelname", "levelno", "lineno", "module", "msecs",
                "pathname", "process", "processName", "relativeCreated",
                "thread", "threadName", "exc_info", "exc_text", "stack_info",
                "getMessage",
            ):
                log_obj[key] = value
        
        return json.dumps(log_obj, ensure_ascii=False)


class PlainFormatter(logging.Formatter):
    """Читаемый формат для консоли"""
    
    FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    DATE_FORMAT = "%H:%M:%S"
    
    def __init__(self):
        super().__init__(self.FORMAT, self.DATE_FORMAT)


def setup_logging(
    level: str = "INFO",
    log_file: str = None,
    json_format: bool = False,
) -> logging.Logger:
    """
    Настроить логирование.
    
    Args:
        level: Уровень логирования (DEBUG/INFO/WARNING/ERROR)
        log_file: Путь к файлу логов (None = только консоль)
        json_format: Использовать JSON формат для файлов
    
    Returns:
        Root logger
    """
    root_logger = logging.getLogger("sales_bot")
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    
    # Убираем старые хендлеры
    root_logger.handlers.clear()
    
    # Консольный хендлер (читаемый формат)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(PlainFormatter())
    root_logger.addHandler(console_handler)
    
    # Файловый хендлер (JSON формат, ротация)
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        
        if json_format:
            file_handler.setFormatter(JSONFormatter())
        else:
            file_handler.setFormatter(PlainFormatter())
        
        root_logger.addHandler(file_handler)
    
    return root_logger


def get_logger(name: str) -> logging.Logger:
    """Получить именованный логгер"""
    return logging.getLogger(f"sales_bot.{name}")
