# SPOTLIGHT — sales-bot-engine

## Architecture pearls (2-3 files/decisions that make this project tick)

**1. `src/core/orchestrator.py` — `SalesBotOrchestrator` как единый оркестратор**

Центральный production-entry: один процесс поднимает N персон параллельно через `discover_personas()` → `PersonaRuntime` на каждую YAML-конфигурацию. Паттерн **multi-tenant в одном контейнере**: персона = YAML-контракт + platform adapter + свой `MemoryFacade`/`DegradedMemoryFacade` + опционально скомпилированный LangGraph.

Pipeline задокументирован прямо в модуле:

```
dedup → preprocess → [semantic_retrieval, anaphora] → route →
antispam → generate → validate → send → memory
```

Реализован в `src/graph/builder.py` (`StateGraph`, `AsyncPostgresSaver`, thread ID `{persona_name}:{user_id}:{chat_id}`). При недоступном Postgres — **graceful degrade** на `_handle_message_legacy()` с `DegradedMemoryFacade` (dedup-only). Это сознательный trade-off: бот живёт, но без LangGraph и без персистентной памяти DM.

**2. `src/core/persona_manager.py` + `personas/*/persona.yaml` — YAML-driven personas**

Персоны описываются декларативно и валидируются Pydantic-моделями (`PersonaConfig`, `VibePersona`, `GreetingPolicy`, `AntiSpamConfig` из `src/core/vibe_schema.py`). Функция `discover_personas()` сканирует `PERSONAS_DIR` рекурсивно по `persona.yaml`. Секреты подтягиваются из env по конвенции `{PERSONA_NAME}_API_ID/HASH/PHONE` — персона не захардкожена в коде, только в `docker-compose.yml` перечислены конкретные бизнес-имена (Kormoved, FitBro, Lera).

**3. `src/main.py` — fail-fast bootstrap + сигналы**

`validate_config()` проверяет `OPENROUTER_API_KEY`, наличие `persona.yaml`, writable `MEMORY_DIR` и log dir **до** старта оркестратора. `setup_signal_handlers()` → `orchestrator.stop()` на SIGTERM/SIGINT. Конфиг логирования берётся из `config/settings.py` через `load_config()` с lazy `default_factory` на env vars — удобно для pytest-патчей.

---

## Hidden risks (1-2 places that could bite)

**1. Сломанное рабочее дерево: удалён `src/models/message.py`**

`SalesBotOrchestrator` и `PersonaManager` импортируют `from ..models.message import IncomingMessage`, но в git status файл помечен как удалён, а каталог `src/models/` отсутствует. **Проект в текущем состоянии не импортируется.** Это не видно из Dockerfile/compose, но блокирует любой запуск.

**2. Двойной memory-stack и legacy path**

Одновременно живут три слоя:
- `UserMemoryStore` — SQLite WAL, funnel stages, entity extraction (`src/memory/user_memory.py`)
- `MemoryFacade` — Supabase PostgreSQL + embeddings (`src/memory/memory_facade.py`, `src/memory/supabase_memory.py`)
- `DegradedMemoryFacade` — stub без DM-персистенции (`src/memory/degraded_memory.py`)

Funnel-тесты (`tests/test_funnel_signals.py`) бьют в `UserMemoryStore.analyze_funnel_signals()` → делегирует в `src/core/funnel_heuristic.suggest_funnel_stage()`. В LangGraph-пути funnel идёт через async `MemoryFacade` [uncertain — нужно сверить `memory_node`]. Риск: **расхождение поведения** между legacy и graph path, плюс keyword-heuristic funnel легко ломается на перефразировках.

**3. Docker resource limits vs embeddings**

`docker-compose.yml` лимит **512M RAM**, при этом `requirements.txt` тянет `sentence-transformers>=3.0.0` с `EMBEDDING_DEVICE=cpu`. На slim-образе Python 3.12 это может OOM при загрузке модели `deepvk/USER-bge-m3`. Healthcheck (`python3 -m src.core.health`) не проверяет embedding-провайдер [uncertain].

**4. Жёсткая привязка секретов в compose**

Env-блок в `docker-compose.yml` явно перечисляет `KORMOVED_*`, `FITBRO_*`, `LERA_*`. Добавление новой персоны требует правки compose, хотя код задуман как persona-agnostic через YAML + env prefix.

---

## Reuse gold (what could be copied to another project)

| Паттерн | Где | Зачем копировать |
|---------|-----|------------------|
| **PlatformAdapter registry** | `src/platforms/registry.py`, `create_adapter()`, `UnknownPlatformError` | Расширяемый plugin-registry `(platform, account_type) → factory` без if-else леса |
| **VibeSchema + PromptCompiler + OutputValidators** | `src/core/vibe_schema.py`, `prompt_compiler.py`, `output_validators.py` | Декларативный «характер» бота + compile-time prompt + post-generation guardrails |
| **Lazy env config dataclasses** | `config/settings.py` (`LLMConfig`, `AppConfig`, `load_config()`) | Тестируемый конфиг без side effects при import |
| **PersonaSupervisor + backoff** | `src/core/lifecycle.py` (`PersonaSupervisor`, `SupervisorConfig`) | Restart policy с exponential backoff для long-running asyncio tasks |
| **Pure funnel heuristic** | `src/core/funnel_heuristic.py` (`suggest_funnel_stage`) | Изолированная бизнес-логика, покрытая unit-тестами без LLM |
| **MemoryFacade over backends** | `src/memory/memory_facade.py` | Facade скрывает embeddings + storage; migration path от sync SQLite API |
| **Degraded mode** | `DegradedMemoryFacade`, `_postgres_reachable()` в orchestrator | Production pattern: ping DB at startup → fallback, не crash |
| **Startup validation** | `src/main.py` → `validate_config()`, `ConfigValidationError` | Fail-fast до поднятия Telethon/LangGraph |

Тестовый каркас в `tests/test_funnel_signals.py` — хороший образец: `tmp_path` fixture, без моков LLM, проверка state transitions funnel (`engaged` → `asking_questions`, override `ready_to_buy`).

---

## Key commits vibe (if git history visible)

История читается как **интенсивный refactor → sprint-based hardening**:

- Ранние **Night cycle N** (10–12): итеративная полировка DM flow, funnel auto-progression, typing indicator — быстрые ночные коммиты.
- **v3 Architecture** + порт паттернов из `ai-tutor-engine` (VibeSchema, PromptCompiler) — заимствование проверенных абстракций, не изобретение с нуля.
- **`d14c64b` Production Refactor: Consolidate v1/v2/v3** — болезненная, но правильная консолидация; следом **`5c7b859` Supabase + LangGraph migration**.
- Серия **`fix: critical defects from code review`** (22050a3 → 1063985) — реактивный цикл после ревью, не TDD-first.
- **Sprint 2 → 3 → 4** (`2fcbeb9`, `451e2cd`, `ce44842`): CI, observability, persona isolation, Postgres degrade, embeddings, quality snapshots — переход от «работает» к «production-ready».
- Свежий **`fd88909` docs: PROJECT.md, STATE.md, COCKPIT.md** — документация догоняет код; один коммит впереди `vds/master`.

Стиль сообщений: **Conventional Commits** (`feat:`, `fix:`, `refactor:`, `docs:`). Ритм — крупные feature/refactor коммиты, затем пачки fix-after-review.

---

## Questions for the author

1. **`src/models/message.py` удалён локально** — это WIP-рефакторинг (перенос `IncomingMessage` в другое место) или случайное удаление? Без него `python -m src.main` падает на import.

2. **Какой memory backend — source of truth в production?** `UserMemoryStore` (SQLite per persona) vs `MemoryFacade` (Supabase + pgvector embeddings). Funnel-тесты покрывают только SQLite-путь; используется ли `analyze_funnel_signals` в LangGraph `memory_node`?

3. **Legacy path (`_handle_message_legacy`) — временный fallback или permanent?** `Dockerfile` ставит `BOT_MODE=v2`, но orchestrator выбирает graph vs legacy по `DATABASE_URL` + ping Postgres. Планируется ли убрать legacy после стабилизации Sprint 4?
