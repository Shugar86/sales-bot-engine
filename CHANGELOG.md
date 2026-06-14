# Changelog

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.0.0/).

## [Unreleased]

### Added
- Vibe-first документация: `README.md`, `AGENTS.md`, `LICENSE`, `CONTRIBUTING.md`, `CHANGELOG.md`.

## [0.4.0] — 2026-03-26

### Added
- **Sprint 4** — production hardening: изоляция персон, graceful degrade при недоступности Postgres, эмбеддинги, снапшоты качества.
- Скрипты `scripts/health_check.py` и `scripts/quality_snapshot.py` для observability.

### Changed
- Унификация проектного формата: `PROJECT.md`, `STATE.md`, `COCKPIT.md`.

## [0.3.0] — 2026-03-18

### Added
- **Sprint 3** — качество диалога, масштаб персон, отказоустойчивость.
- **Sprint 2** — CI, схождение legacy-форматов, observability.

### Changed
- Production Refactor: консолидация v1/v2/v3 в единую архитектуру.
- Supabase + LangGraph migration: переход на production-пайплайн с персистентным состоянием.

### Fixed
- Исправления routing, persona contract hygiene, legacy parity.

## [0.2.0] — 2026-02

### Added
- **v3 Architecture** — паттерны из `ai-tutor-engine`: `VibeSchema`, `PromptCompiler`, `ContextManager`, `OutputValidators`.
- `PlatformAdapter` registry: добавление платформы без ветвлений в графе.
- DM flow, typing indicator, автопрогрессия воронки.

### Fixed
- Серия исправлений критических дефектов после code review.

## [0.1.0] — 2026-01

### Added
- Первоначальная реализация sales-bot engine.
- Поддержка Telegram userbot и Bot API.
- Базовые персоны: кормовед, фитнес-коуч, SMM-специалист.
- YAML-контракт персоны и anti-spam механика.

---

## Планируемые темы для ближайших релизов

- [ ] Стабилизация `PlatformAdapter` registry и документация по добавлению платформ.
- [ ] Единый source of truth для памяти (SQLite vs PostgreSQL).
- [ ] Удаление legacy path после стабилизации Sprint 4.
- [ ] CI: optional real-LLM тесты и качественные метрики диалога.
