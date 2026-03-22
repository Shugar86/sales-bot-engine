"""
Sales Bot Engine — Production Entry Point
Запуск: python -m src.main

Unified orchestrator for multi-persona sales bot.
Telegram-first architecture with per-persona isolation.

Environment variables:
  - OPENROUTER_API_KEY: Required. API key for LLM calls.
  - PERSONAS_DIR: Directory containing persona YAML configs (default: ./personas)
  - MEMORY_DIR: Directory for persistent storage (default: ./data/memory)
  - LOG_LEVEL: Logging level (default: INFO)
  - LOG_FILE: Log file path (default: data/logs/sales_bot.log)

Each persona must have valid credentials in environment:
  - Userbot: {PERSONA_NAME}_API_ID, {PERSONA_NAME}_API_HASH, {PERSONA_NAME}_PHONE
  - Bot: TELEGRAM_BOT_TOKEN
  - VK: {PERSONA_NAME}_VK_TOKEN
"""

import asyncio
import os
import signal
import sys
from pathlib import Path

from config.settings import load_config, LLMConfig
from src.utils.logger import setup_logging, get_logger
from src.core.orchestrator import SalesBotOrchestrator, run_orchestrator


logger = get_logger("main")


class ConfigValidationError(Exception):
    """Raised when required configuration is missing or invalid."""
    pass


def validate_config() -> None:
    """
    Validate configuration on startup.

    Raises:
        ConfigValidationError: If required configuration is missing.
    """
    errors = []

    # Check API key
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        errors.append("OPENROUTER_API_KEY is not set")
    elif not api_key.startswith("sk-"):
        errors.append("OPENROUTER_API_KEY appears invalid (should start with 'sk-')")

    # Check personas directory
    personas_dir = Path(os.getenv("PERSONAS_DIR", "./personas"))
    if not personas_dir.exists():
        errors.append(f"PERSONAS_DIR does not exist: {personas_dir}")
    elif not personas_dir.is_dir():
        errors.append(f"PERSONAS_DIR is not a directory: {personas_dir}")
    else:
        # Check for at least one persona.yaml
        persona_files = list(personas_dir.rglob("persona.yaml"))
        if not persona_files:
            errors.append(f"No persona.yaml files found in {personas_dir}")

    # Check memory directory is writable
    memory_dir = Path(os.getenv("MEMORY_DIR", "./data/memory"))
    try:
        memory_dir.mkdir(parents=True, exist_ok=True)
        test_file = memory_dir / ".write_test"
        test_file.write_text("")
        test_file.unlink()
    except Exception as e:
        errors.append(f"MEMORY_DIR is not writable: {memory_dir} ({e})")

    # Check log directory is writable
    log_file = os.getenv("LOG_FILE", "data/logs/sales_bot.log")
    log_dir = Path(log_file).parent
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        errors.append(f"Log directory is not writable: {log_dir} ({e})")

    if errors:
        raise ConfigValidationError("\n".join(f"  - {e}" for e in errors))


def setup_signal_handlers(orchestrator: SalesBotOrchestrator, loop: asyncio.AbstractEventLoop) -> None:
    """Setup signal handlers for graceful shutdown."""
    def signal_handler(sig: signal.Signals) -> None:
        logger.info(f"Received signal {sig.name}, initiating graceful shutdown...")
        asyncio.create_task(orchestrator.stop())

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass


async def main() -> int:
    """
    Main entry point.

    Returns:
        Exit code (0 for success, 1 for error).
    """
    # Load config for logging setup
    config = load_config()

    setup_logging(
        level=config.log_level,
        log_file=config.log_file,
        json_format=True,
    )

    logger.info("=" * 60)
    logger.info("Sales Bot Engine — Production Startup")
    logger.info("=" * 60)

    # Validate configuration
    try:
        validate_config()
        logger.info("Configuration validation: PASSED")
    except ConfigValidationError as e:
        logger.error(f"Configuration validation FAILED:\n{e}")
        print(f"ERROR: Configuration validation failed:\n{e}", file=sys.stderr)
        return 1

    # Get directories and API key
    personas_dir = os.getenv("PERSONAS_DIR", "./personas")
    memory_dir = os.getenv("MEMORY_DIR", "./data/memory")
    api_key = os.getenv("OPENROUTER_API_KEY", "")

    logger.info(f"Personas directory: {personas_dir}")
    logger.info(f"Memory directory: {memory_dir}")
    logger.info(f"Log level: {config.log_level}")

    # Create orchestrator
    orchestrator = SalesBotOrchestrator(
        personas_dir=personas_dir,
        memory_dir=memory_dir,
        openrouter_api_key=api_key,
    )

    # Setup signal handlers
    loop = asyncio.get_event_loop()
    setup_signal_handlers(orchestrator, loop)

    # Run
    exit_code = 0
    try:
        await orchestrator.start()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        exit_code = 1
    finally:
        logger.info("Shutting down...")
        await orchestrator.stop()
        logger.info("Shutdown complete")

    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
