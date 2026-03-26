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
│  Генерит ответ  │  ← persona.yaml + память юзера
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

## YAML-конфигурация

Каждая персона = отдельная директория в `personas/`:
- `personas/<name>/persona.yaml` — полная конфигурация

Структура:
- `persona` — кто он, как говорит, чего нельзя
- `vibe` — emotional tone, taboos
- `behavior` — greeting policy, response examples
- `product` — что продаёт
- `triggers` — когда отвечать, когда молчать
- `conversation_flow` — как вести диалог (группа vs ЛС)

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

## Статус

🚧 В активной разработке. См. `ARCHITECTURE.md` и `MIGRATION.md`.

## Runtime Inventory

- **Production:** `src/core/orchestrator.py` — multi-persona, `PersonaRuntime.adapter` + LangGraph
- **Платформы:** `src/platforms/` — `create_adapter()`, драйверы остаются в `src/monitors/`

## Автор

Heisenberg + Doc (AI). R&D проект. Март 2026.
