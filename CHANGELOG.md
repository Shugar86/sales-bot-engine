# Changelog

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.0.0/).

## [Unreleased]

### Added
- Vibe-first документация: `README.md`, `AGENTS.md`, `LICENSE`, `CONTRIBUTING.md`, `CHANGELOG.md`.

## [Ранние этапы] — 2026

### Added / Changed
- **Sprint 4** — production hardening: изоляция персон, graceful degrade при недоступности Postgres, эмбеддинги, снапшоты качества.
- **Sprint 3** — качество диалога, масштаб персон, отказоустойчивость.
- **Sprint 2** — CI, схождение legacy-форматов, observability.
- **Production Refactor** — консолидация v1/v2/v3 в единую архитектуру.
- **Supabase + LangGraph migration** — переход на production-пайплайн с персистентным состоянием.
- **v3 Architecture** — паттерны из `ai-tutor-engine`: `VibeSchema`, `PromptCompiler`, `ContextManager`, `OutputValidators`.
- **Night cycles 11–12** — DM flow, typing indicator, автопрогрессия воронки.

### Fixed
- Серия исправлений критических дефектов после code review.
- Исправления routing, persona contract hygiene, legacy parity.

---

Планируемые темы для ближайших релизов:

- [ ] Стабилизация `PlatformAdapter` registry и документация по добавлению платформ.
- [ ] Единый source of truth для памяти (SQLite vs PostgreSQL).
- [ ] Удаление legacy path после стабилизации Sprint 4.
- [ ] CI: optional real-LLM тесты и качественные метрики диалога.
