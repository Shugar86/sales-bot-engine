"""
Sales Bot Engine — Entry Point
Запуск: python -m src.main

Modes:
  - v2 (default): Multi-persona userbot swarm (canonical)
  - v1: Single persona via Bot API (legacy)
"""

import asyncio
import os
import signal
import sys

from config.settings import load_config
from src.utils.logger import setup_logging, get_logger


async def run_v1():
    """Run v1: Single persona, Bot API."""
    from src.core.orchestrator_legacy import SalesBotOrchestrator
    
    config = load_config()
    setup_logging(
        level=config.log_level,
        log_file=config.log_file,
        json_format=True,
    )
    
    logger = get_logger("main")
    logger.info("Sales Bot Engine v1 starting (single persona, Bot API)...")
    logger.info(f"Contract: {config.contract_path}")
    
    bot = SalesBotOrchestrator(config)
    
    loop = asyncio.get_event_loop()
    
    def signal_handler():
        logger.info("Shutdown signal received")
        asyncio.create_task(bot.shutdown())
    
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass
    
    try:
        await bot.run()
    except KeyboardInterrupt:
        logger.info("Interrupted")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        await bot.shutdown()


async def run_v2():
    """Run v2: Multi-persona userbot swarm (full pipeline with humanizer, preprocess, anaphora)."""
    from src.core.orchestrator_v2 import run_multi_persona
    
    config = load_config()
    setup_logging(
        level=config.log_level,
        log_file=config.log_file,
        json_format=True,
    )
    
    personas_dir = os.getenv("PERSONAS_DIR", "./personas")
    memory_dir = os.getenv("MEMORY_DIR", "./data/memory")
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    
    logger = get_logger("main")
    logger.info("Sales Bot Engine v2 starting (multi-persona swarm)...")
    logger.info(f"Personas dir: {personas_dir}")
    
    await run_multi_persona(
        personas_dir=personas_dir,
        memory_dir=memory_dir,
        api_key=api_key,
    )


async def main():
    """Entry point — select mode via env var."""
    mode = os.getenv("BOT_MODE", "v2").lower()
    
    if mode == "v1":
        await run_v1()
    else:
        await run_v2()


if __name__ == "__main__":
    asyncio.run(main())
