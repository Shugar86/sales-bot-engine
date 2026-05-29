# PROJECT — sales-bot-engine

Anchor (immutable core). Changing meaning = new project.

## Mission (1-2 sentences, north star)

Оцифровать менеджера по холодным чат-продажам: бот, который ведёт себя как живой эксперт в тематических чатах (TG/VK), отвечает по делу, помнит контекст и мягко ведёт к продукту в ЛС — без спам-паттернов и банов.

## Immutable core (3-6 principles that cannot break)

1. **Человек, не бот** — ответы должны проходить «тест Тьюринга»; запрещены шаблонный маркетинг, простыни и явные бот-паттерны.
2. **Экспертность > продажа** — сначала ценность и диалог, продажа — следствие доверия, не триггер по умолчанию.
3. **YAML-контракт персоны** — поведение, vibe, триггеры и продукт задаются в `personas/*/persona.yaml`, а не хардкодом в коде.
4. **Изоляция персон** — каждая персона = свой runtime, память, rate limits и адаптер платформы; сбой одной не должен валить swarm.
5. **Лучше молчать, чем нести мусор** — при ошибке/таймауте LLM ответ не отправляется; валидация выхода обязательна.
6. **Платформа через адаптер** — отправка и inbound только через `PlatformAdapter`; граф и оркестратор не ветвятся по `if platform == …`.

## Key technical decisions (table: Decision | Why)

| Decision | Why |
|----------|-----|
| LangGraph + PostgreSQL checkpoint (`DATABASE_URL`) | Прод-пайплайн с персистентным состоянием, трассировкой нод и восстановлением |
| Fast Router (Gemini Flash) + Slow Generator (Claude Sonnet) via OpenRouter | Дешёвая фильтрация «отвечать/нет» + качественная генерация редко |
| Plain-text generation (не JSON-first) | Естественные сообщения в чат; JSON-артефакты снимаются постобработкой |
| `PlatformAdapter` registry (`platform` + `account_type`) | Расширяемость TG/VK/новых сетей без правок графа |
| SQLite per persona (`MEMORY_DIR`) + Supabase/Postgres (`MemoryFacade`) | Dedup и legacy/degraded path локально; контекст, DM, эмбеддинги — в Postgres |
| Funnel stage из памяти + эвристика (`funnel_heuristic.py`) | Стадия воронки не доверяется полю `stage` от LLM |
| Legacy path только без `DATABASE_URL` или при Postgres degrade на старте | Явная деградация вместо тихого отката при заданном URL |
| CI: ruff + pytest (`not integration`) + optional Postgres integration job | Семантическая валидация YAML и регрессии без боевых ключей |

## Stack (languages, frameworks, key libs)

- **Язык:** Python 3.11+
- **Контейнеризация:** Docker, docker-compose
- **Оркестрация LLM/граф:** LangGraph, langchain-core, langgraph-checkpoint-postgres
- **LLM API:** OpenRouter (httpx)
- **Мессенджеры:** Telethon (userbot), Telegram Bot API, VK API [частично]
- **Память:** asyncpg, SQLite (dedup/user context), sentence-transformers (эмбеддинги), pgvector [optional]
- **Конфиг:** PyYAML, Pydantic v2
- **Тесты/CI:** pytest, pytest-asyncio, ruff

## Key files / entry points

| Путь | Назначение |
|------|------------|
| `src/main.py` | Точка входа: `python -m src.main` |
| `src/core/orchestrator.py` | Multi-persona orchestrator, LangGraph invoke, message_trace |
| `src/core/lifecycle.py` | Supervision, restart, health per persona |
| `src/graph/builder.py`, `src/graph/nodes.py` | Сборка и ноды LangGraph-пайплайна |
| `src/platforms/registry.py` | `create_adapter()` по YAML |
| `src/core/persona_manager.py` | Загрузка `personas/*/persona.yaml`, env overrides |
| `src/memory/memory_facade.py` | Единый API памяти для графа |
| `config/settings.py` | Загрузка конфигурации окружения |
| `personas/{kormoved,fitness,smm_blogger}/persona.yaml` | Боевые персоны |
| `scripts/health_check.py` | CLI health (JSON/table) |
| `scripts/quality_snapshot.py` | Быстрый снимок качества DM-ответов |
| `Dockerfile`, `docker-compose.yml` | Прод-деплой (multi-persona в одном контейнере) |
| `supabase/migrations/*.sql` | Схема Postgres / checkpoints |

## Documentation (existing docs links)

- [README.md](README.md) — обзор, быстрый старт, структура
- [ARCHITECTURE.md](ARCHITECTURE.md) — актуальная production-архитектура
- [ARCHITECTURE_V3.md](ARCHITECTURE_V3.md) — философия v3 «живой человек в чате»
- [MIGRATION.md](MIGRATION.md) — миграция legacy → unified architecture
- [PERSONA_EXTENSION.md](PERSONA_EXTENSION.md) — добавление персоны
- [PLATFORM_EXTENSION.md](PLATFORM_EXTENSION.md) — добавление платформы/адаптера

## Health (how to check it works)

1. **Unit-тесты:** `pytest -m "not integration" --tb=short` (как в CI)
2. **Health CLI:** `python scripts/health_check.py` — personas, writable `MEMORY_DIR`, Postgres (если `DATABASE_URL`), optional LLM probe; exit 0/1
3. **Runtime snapshot:** оркестратор пишет JSON в `SALES_BOT_HEALTH_FILE` (default `/tmp/sales-bot-health.json`); health_check подмешивает `orchestrator.*`
4. **Docker healthcheck:** `python3 -m src.core.health` (в `docker-compose.yml`)
5. **Качество диалога:** `python scripts/quality_snapshot.py --persona <slug> [--mock]`
6. **Логи/trace:** `event=message_trace` с полями `path`, `nodes`, `latency_ms`, `llm_error`
