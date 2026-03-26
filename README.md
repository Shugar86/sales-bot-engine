# 🤖 Sales Bot Engine

Оцифровка менеджера по холодным чат-продажам.

## Концепция

Бот-продавец, который:
1. Мониторит чаты (TG/VK) кинологов/собаководов
2. Триггерится на релевантные сообщения (через Fast Model)
3. Генерит экспертные ответы (через Slow Model + YAML контракт)
4. Помнит с кем говорил и что обсуждал
5. При переходе в ЛС — мягко продаёт продукт
6. Его не банят за спам, потому что он не спамит — он эксперт

## Архитектура

```
┌─────────────────────────────────────────┐
│         Orchestrator                    │
│  (multi-persona swarm, production)      │
├──────────┬──────────┬──────────┬───────┤
│ Persona 1│ Persona 2│ Persona 3│  ...  │
│ Кормовед │ Фитнес   │ SMM      │       │
├──────────┴──────────┴──────────┴───────┤
│   PlatformAdapter (реестр по YAML)     │
│   run / send_reply / capabilities      │
├──────────┴──────────┴──────────┴───────┤
│        Drivers (src/monitors/)         │
├──────────┬──────────┬──────────┬───────┤
│ Telethon │ Bot API  │ VK API   │ …     │
└──────────┴──────────┴──────────┴───────┘
```

Новая платформа: модуль в `src/platforms/adapters/` + запись в `src/platforms/registry.py` (ключ `platform` + `account_type` из `persona.yaml`). Граф и оркестратор не содержат `elif platform == …` для отправки сообщений.

### Пайплайн обработки сообщения

```
Сообщение в чате
    ↓
┌─────────────────┐
│  Dedup          │  (не отвечаем дважды)
└────────┬────────┘
         ↓
┌─────────────────┐
│  Preprocess     │  (shortcut-обработка)
└────────┬────────┘
         ↓
┌─────────────────┐
│  Fast Router    │  (Gemini Flash — дёшево и быстро)
│  Отвечать? Нет? │
└────────┬────────┘
         ↓ (если да)
┌─────────────────┐
│  Slow Generator │  (Claude — качественно)
│  Свободный текст│  ← persona.yaml + память (не JSON-контракт в ответе)
└────────┬────────┘
         ↓
┌─────────────────┐
│  Output Validator│  (banned phrases, greeting policy)
└────────┬────────┘
         ↓
┌─────────────────┐
│  Anti-Spam      │  (typing simulation, delays; эмодзи — если adapter.capabilities)
└────────┬────────┘
         ↓
    Отправка через PlatformAdapter → Память (Supabase + эмбеддинги; см. DATABASE_URL)
```

Пайплайн в проде реализован как **LangGraph** с чекпоинтером в PostgreSQL (`DATABASE_URL`). Подробности — `ARCHITECTURE.md`.

### Деградация, снимок здоровья и качество

- **Postgres недоступен при старте** (при заданном `DATABASE_URL`): персона остаётся в процессе на **legacy**-пути (dedup + `DegradedMemoryFacade`), граф не собирается; в статусе `postgres_degraded: true`.
- Оркестратор пишет JSON в `SALES_BOT_HEALTH_FILE` (по умолчанию `/tmp/sales-bot-health.json`); `python scripts/health_check.py` подмешивает этот файл в отчёт (`orchestrator.*`).
- Скрипт `python scripts/quality_snapshot.py --persona <slug> [--mock]` — быстрый чек ответов DM по YAML (в тестах используется `--mock` без сети).

## YAML-конфигурация

Каждая персона = отдельная директория в `personas/`:
- `personas/<name>/persona.yaml` — полная конфигурация

Структура:
- `persona` — кто он, как говорит, чего нельзя
- `vibe` — emotional tone, taboos
- `behavior` — greeting policy, response examples
- `product` — что продаёт
- `triggers` — когда отвечать, когда молчать
- `conversation_flow` — лимиты группы (`group_mode.max_messages_per_hour`), стиль, тексты для ЛС; блок `dm_mode.funnel` — **подсказки для промпта**, а не state machine. Стадия воронки в DM берётся из памяти и после ответа обновляется **эвристикой** (`src/core/funnel_heuristic.py`), а не из поля `stage` ответа LLM.
- `memory.entity_profile` — опционально `dog` | `fitness` | `generic`: выбор эвристического экстрактора сущностей в SQLite без правок кода.
- `anti_spam.dm_max_inbound_burst_without_bot_reply` — лимит подряд входящих DM без исходящего ответа бота (персистентный счётчик в `users.extra` / SQLite).

## Секреты и аккаунты

В `personas/*/persona.yaml` **не храните** телефон, токены бота, API hash. Задайте их в `.env` (шаблон — `.env.example`):

- Общие: `OPENROUTER_API_KEY`, `DATABASE_URL`, при необходимости `TELEGRAM_API_ID` / `TELEGRAM_API_HASH`.
- На персону: префикс = **верхний регистр `session_name`** из YAML (`kormoved` → `KORMOVED_PHONE`, `KORMOVED_API_HASH`, …). Значения из YAML подменяются при загрузке через `apply_env_overrides_to_persona`.

## Модели

| Роль | Модель | Зачем |
|------|--------|-------|
| Router | Gemini Flash | Дёшево, быстро, часто |
| Generator | Claude Sonnet | Качественно, редко |

## Быстрый старт

```bash
pip install -r requirements.txt

# Установить переменные
export OPENROUTER_API_KEY="sk-or-..."
export PERSONAS_DIR="./personas"
export MEMORY_DIR="./data/memory"
# Память LangGraph + Supabase (PostgreSQL, при необходимости pgvector)
export DATABASE_URL="postgresql://..."

# Запуск
python -m src.main
```

## Структура

```
sales-bot-engine/
├── personas/            # YAML-конфиги персонажей
│   ├── kormoved/        # Продавец кормов
│   ├── fitness/         # Фитнес-эксперт
│   └── smm_blogger/     # SMM-специалист
├── src/
│   ├── core/            # Router, Orchestrator, PersonaManager
│   ├── platforms/       # PlatformAdapter, registry, адаптеры TG/VK
│   ├── graph/           # LangGraph ноды и сборка графа
│   ├── monitors/        # Низкоуровневые драйверы (Telethon, Bot API, VK)
│   ├── responders/      # Generator, Composer
│   └── memory/          # MemoryFacade, Supabase, эмбеддинги
├── data/
│   ├── memory/          # SQLite DBs per persona
│   └── logs/            # Logs
└── tests/
```

## Валидация персон (CI)

Структура и **семантическая полнота** YAML проверяются в тестах (`tests/test_persona_yaml_schema.py` → `src/core/persona_yaml_validate.py`): минимум примеров, триггеры, `max_messages_per_hour`, `vibe`, предупреждения по group-context и «бот»-примерам. Боевые файлы в `personas/` не должны ломать `pytest -m "not integration"`.

## Статус

🚧 В активной разработке. См. `ARCHITECTURE.md`, `MIGRATION.md`, `PERSONA_EXTENSION.md`, `PLATFORM_EXTENSION.md`.

## Runtime Inventory

- **Production:** `src/core/orchestrator.py` — multi-persona, `PersonaRuntime.adapter` + **LangGraph** при заданном `DATABASE_URL` и успешной сборке графа.
- **Legacy:** линейный `_handle_message_legacy` **только если `DATABASE_URL` не задан** (в логах `running legacy path, reason: DATABASE_URL not set`). Если URL есть, а граф не собрался — ошибка обработки, без отката в legacy.
- **Наблюдаемость:** по завершении обработки сообщения — JSON trace (`event=message_trace` / поля `path`, `nodes`, `latency_ms`, `llm_error`, …).
- **LLM:** при ошибке/таймауте генератор возвращает `None`, ответ пользователю не отправляется (лучше молчание, чем мусор).
- **Платформы:** `src/platforms/` — `create_adapter()`; платформа — `PLATFORM_EXTENSION.md`, новая персона — `PERSONA_EXTENSION.md`.
- **Health CLI:** `python scripts/health_check.py` (JSON/table, опционально Postgres и probe LLM).

## Автор

Heisenberg + Doc (AI). R&D проект. Март 2026.
