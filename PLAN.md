# Sales Bot Engine — Статус

## ✅ Готово (2840 строк Python, 45 тестов)

### Ядро (Canonical: v2)
- [x] `config/settings.py` — все настройки из env vars
- [x] `src/core/router.py` — Fast Model Router (Gemini Flash)
- [x] `src/core/orchestrator_v2.py` — Multi-persona orchestrator (canonical)
- [x] `src/core/persona_manager.py` — Persona loading (YAML → Pydantic)
- [x] `src/core/prompt_compiler.py` — Prompt assembly
- [x] `src/core/output_validators.py` — Post-generation validation
- [x] `src/responders/generator.py` — Slow Model Generator (Claude)
- [x] `src/memory/user_memory.py` — User Memory (SQLite persistence)
- [x] `src/monitors/telegram_monitor.py` — Telegram long polling
- [x] `src/monitors/anti_spam.py` — Rate limiter + random delays
- [x] `src/contracts/loader.py` — Legacy YAML contract loading (v1 only)
- [x] `src/utils/llm_client.py` — OpenRouter client (retry/backoff)
- [x] `src/utils/logger.py` — Structured logging (JSON + plain)
- [x] `src/utils/dedup.py` — Message deduplication
- [x] `src/main.py` — Entry point (defaults to v2)

### Тесты (45/45 ✅)
- [x] `tests/test_router.py` — Router decisions, parsing, fallbacks
- [x] `tests/test_memory.py` — CRUD, dog info extraction, funnel stages, persistence
- [x] `tests/test_contracts.py` — Loading, validation, hot-reload
- [x] `tests/test_integration.py` — E2E: respond flow, ignore flow, DM with context, dedup

### Контракты (Legacy v1)
- [x] `contracts/korm/persona.yaml` — "Кормовед" (v1 format, legacy — use `personas/kormoved/persona.yaml` for v2)

## 🔜 Ждём от Лысого
- [ ] Telegram Bot Token (для тестового бота)
- [ ] OpenRouter API Key (для бота, отдельный от Doc)
- [ ] ID чатов для мониторинга
- [ ] Твинк аккаунт для тестов

## Запуск (Canonical v2)
```bash
export OPENROUTER_API_KEY="sk-or-..."
export PERSONAS_DIR="./personas"
export MEMORY_DIR="./data/memory"
python3 -m src.main
```
