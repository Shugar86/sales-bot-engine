# Sales Bot Engine — Статус

## ✅ Готово (2840 строк Python, 45 тестов)

### Ядро
- [x] `config/settings.py` — все настройки из env vars
- [x] `src/core/router.py` — Fast Model Router (Gemini Flash)
- [x] `src/core/orchestrator.py` — Main event loop (state machine)
- [x] `src/responders/generator.py` — Slow Model Generator (Claude)
- [x] `src/memory/user_memory.py` — User Memory (JSON persistence)
- [x] `src/monitors/telegram_monitor.py` — Telegram long polling
- [x] `src/monitors/anti_spam.py` — Rate limiter + random delays
- [x] `src/contracts/loader.py` — YAML contract loading + validation + hot-reload
- [x] `src/utils/llm_client.py` — OpenRouter client (retry/backoff)
- [x] `src/utils/logger.py` — Structured logging (JSON + plain)
- [x] `src/utils/dedup.py` — Message deduplication
- [x] `src/main.py` — Entry point

### Тесты (45/45 ✅)
- [x] `tests/test_router.py` — Router decisions, parsing, fallbacks
- [x] `tests/test_memory.py` — CRUD, dog info extraction, funnel stages, persistence
- [x] `tests/test_contracts.py` — Loading, validation, hot-reload
- [x] `tests/test_integration.py` — E2E: respond flow, ignore flow, DM with context, dedup

### Контракты
- [x] `contracts/korm/persona.yaml` — "Кормовед" (продавец корма для овчарок-сапёров)
- [x] `contracts/AGENT_CONTRACT.yaml` — Контракт для субагента-разработчика

## 🔜 Ждём от Лысого
- [ ] Telegram Bot Token (для тестового бота)
- [ ] OpenRouter API Key (для бота, отдельный от Doc)
- [ ] ID чатов для мониторинга
- [ ] Твинк аккаунт для тестов

## Запуск
```bash
export OPENROUTER_API_KEY="sk-or-..."
export TELEGRAM_BOT_TOKEN="123:ABC"
export MONITOR_CHATS="-100123456789"
export CONTRACT_PATH="contracts/korm/persona.yaml"
python3 -m src.main
```
