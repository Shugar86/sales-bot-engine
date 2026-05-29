# STATE — sales-bot-engine

*Updated: 2026-05-30*

## Status (active/paused/experimental/archived + why)

**Active / experimental (R&D).** Проект в активной разработке: unified production-архитектура собрана, CI зелёный на unit-уровне, боевой деплой через Docker возможен, но это internal R&D, не SaaS-продукт (см. README, ARCHITECTURE.md).

## What is happening now (branch, commits focus, uncommitted)

- **Ветка:** `master` @ `ce44842` — *feat: Sprint 4 production hardening (isolation, Postgres degrade, embeddings, quality)*
- **Недавний фокус коммитов (Sprint 2→4):**
  - CI, legacy convergence, observability
  - PlatformAdapter registry, docs sync
  - DM routing, persona contract hygiene
  - Dialogue quality, persona scale, resilience
  - Isolation, Postgres degrade path, embeddings, quality snapshots
- **Uncommitted (working tree):**
  - `D data/memory/processed_messages.json` — удалён legacy JSON dedup (миграция на SQLite завершена в коде)
  - `D src/models/__init__.py`, `D src/models/message.py` — **критично:** импорты `IncomingMessage` остаются в `orchestrator.py`, `graph/nodes.py`, `graph/state.py`, адаптерах; без восстановления/переноса модели проект не стартует

## Blockers (what blocks progress)

1. **Удалённый `src/models/message.py` без замены** — блокирует импорт и запуск; нужно закоммитить перенос модели или откатить deletion.
2. **Production credentials** — для боевого прогона нужны env per persona (`{SESSION_NAME}_API_*`, `OPENROUTER_API_KEY`, опционально `DATABASE_URL`); в репозитории их нет (by design).
3. **Пустые `groups_to_monitor` в persona YAML** — персоны настроены, но целевые чаты закомментированы/пусты; мониторинг в проде требует явной конфигурации.
4. **VK adapter** — заявлен как extension point; полнота боевой готовности [uncertain] без отдельной проверки.
5. **Документация vs код:** `PLATFORM_EXTENSION.md` ссылается на `src/models/message.py` — рассинхрон с uncommitted deletion.

## Last release / milestone

**Sprint 4 — Production hardening** (`ce44842`, март 2026):

- Per-persona isolation и supervisor resilience
- Postgres degrade → legacy path + `DegradedMemoryFacade` при недоступности БД на старте
- Embeddings factory, semantic memory
- `scripts/quality_snapshot.py`, health snapshot orchestrator
- Unified orchestrator (конvergence v1/v2/v3 завершена в Sprint 2)

## Planned (next 3-5 concrete steps)

1. **Восстановить или перенести `IncomingMessage`** — устранить uncommitted deletion, прогнать `pytest -m "not integration"`.
2. **Закоммитить cleanup** — `processed_messages.json` и финальный статус `src/models/` согласовать с MIGRATION.md.
3. **Заполнить `groups_to_monitor`** для одной персоны (smoke в реальном чате) + проверить message_trace и health snapshot.
4. **Integration job локально/CI** — `pytest -m integration` с Postgres service (как в `.github/workflows/ci.yml`).
5. **Синхронизировать PLATFORM_EXTENSION.md** с фактическим расположением модели сообщений после fix.
