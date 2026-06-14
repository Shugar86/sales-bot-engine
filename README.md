# Sales Bot Engine

> Оцифровка менеджера по холодным чат-продажам, который ведёт себя как живой эксперт — и не получает бан за спам.

## Что это

**Sales Bot Engine** — multi-persona оркестратор для Telegram и ВКонтакте.
Он слушает тематические чаты, выбирает момент, когда его реплика действительно уместна, и отвечает как опытный коллега: коротко, по делу, без шаблонного маркетинга. Если человек уходит в личные сообщения — бот мягко продолжает разговор и помогает ему найти подходящий продукт.

Ключевой принцип: **сначала ценность и доверие, продажа — следствие диалога.**

## Возможности

- **Multi-persona swarm** — каждая персона (кормовед, фитнес-коуч, SMM-специалист) живёт в своём runtime с изолированной памятью и rate limits.
- **LangGraph-пайплайн** с персистентными чекпоинтами в PostgreSQL.
- **Graceful degrade** на legacy-путь, если Postgres недоступен при старте.
- **YAML-контракт персоны** — характер, табу, триггеры, примеры для тьюринг-теста, анти-спам — всё в `personas/*/persona.yaml`.
- **Fast Router + Slow Generator** — Gemini Flash фильтрует "отвечать/нет", Claude/Sonnet пишет текст через OpenRouter.
- **PlatformAdapter registry** — новая платформа добавляется одним модулем, без `if platform == ...` в графе.
- **Human-like behavior** — typing simulation, вариативные задержки, leave-on-read, emoji-реакции, контроль частоты сообщений.
- **Наблюдаемость** — `message_trace` JSON на каждое сообщение, health snapshot, CLI `scripts/health_check.py` и `scripts/quality_snapshot.py`.

## Быстрый старт

```bash
# 1. Клонировать
# (репозиторий приватный — доступ по приглашению)
cd sales-bot-engine

# 2. Подготовить окружение
cp .env.example .env
# отредактируй .env: OPENROUTER_API_KEY, DATABASE_URL, Telegram API и телефоны персон

# 3. Установить зависимости
pip install -r requirements.txt

# 4. Запустить
python -m src.main
```

> Для production-деплоя используй `docker compose up -d` (см. `docker-compose.yml`).

## Архитектура / стек

| Область | Технология |
|---------|------------|
| Язык | Python 3.11+ |
| LLM-роутинг | Gemini Flash 2.0 через OpenRouter |
| LLM-генерация | Claude Sonnet 3.5 через OpenRouter |
| Пайплайн | LangGraph + `langgraph-checkpoint-postgres` |
| Telegram | Telethon (userbot) + Telegram Bot API |
| ВКонтакте | VK API (extension point) |
| Память | PostgreSQL + pgvector, SQLite per persona (dedup/legacy) |
| Эмбеддинги | `sentence-transformers` (`deepvk/USER-bge-m3` по умолчанию) |
| Конфиг | PyYAML + Pydantic v2 |
| CI / тесты | GitHub Actions, pytest, ruff |

Пайплайн обработки сообщения:

```text
Входящее сообщение
    ↓
Dedup → Preprocess → Semantic retrieval / Anaphora
    ↓
Fast Router (отвечать / нет)
    ↓
AntiSpam → Generate → Validate → Send via PlatformAdapter → Memory
```

Подробнее — в [`ARCHITECTURE.md`](./ARCHITECTURE.md).

## Структура проекта

```text
sales-bot-engine/
├── personas/                  # YAML-контракты персон
│   ├── kormoved/              # Консультант по кормам
│   ├── fitness/               # Фитнес-эксперт
│   └── smm_blogger/           # SMM-специалист
├── src/
│   ├── core/                  # Orchestrator, PersonaManager, Router, Generator, Funnel
│   ├── graph/                 # LangGraph: nodes + builder
│   ├── platforms/             # PlatformAdapter, registry, адаптеры TG/VK
│   ├── monitors/              # Низкоуровневые драйверы
│   ├── memory/                # MemoryFacade, Supabase, embeddings, degraded mode
│   ├── responders/            # Composer, валидаторы
│   └── main.py                # Точка входа
├── config/                    # Загрузка конфигурации окружения
├── scripts/                   # health_check.py, quality_snapshot.py
├── tests/                     # Unit + integration тесты
├── supabase/migrations/       # Схема PostgreSQL
├── docker-compose.yml
└── Dockerfile
```

## Примеры

Фрагмент персоны "Кормовед":

```yaml
persona:
  name: "Андрей"
  platform: "telegram"
  account_type: "userbot"
  personality: >
    Бывший кинолог-инструктор. 12 лет со служебными собаками.
    Сейчас консультант по профессиональному кормлению.
    Короткие предложения. Без маркетинга. Говорит как коллега.

vibe:
  voice: "Бывалый кинолог. Говорит тепло, по-деловому, без пафоса."
  taboos: ["политика", "религия", "Маркетинговый жаргон", "Простыни текста"]

behavior:
  on_greeting: "Здравствуйте. Какая собака?"
  on_dm: "Вспомни из какого чата человек. Болтай как в группе."

anti_spam:
  min_delay_between_messages: 30
  leave_on_read: 0.35
  typing_simulation: true
```

## Документация

- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — production-архитектура
- [`PROJECT.md`](./PROJECT.md) — миссия, immutable core, ключевые решения
- [`PERSONA_EXTENSION.md`](./PERSONA_EXTENSION.md) — как добавить персону
- [`PLATFORM_EXTENSION.md`](./PLATFORM_EXTENSION.md) — как добавить платформу
- [`MIGRATION.md`](./MIGRATION.md) — миграция legacy → unified
- [`SPOTLIGHT.md`](./SPOTLIGHT.md) — архитектурные жемчужины и риски
- [`CONTRIBUTING.md`](./CONTRIBUTING.md) — как участвовать
- [`CHANGELOG.md`](./CHANGELOG.md) — история изменений

## Статус

Проект в активной R&D-разработке. API, форматы YAML и внутренние контракты могут меняться — см. [`CHANGELOG.md`](./CHANGELOG.md).

## Лицензия

Проприетарная, все права защищены — см. [`LICENSE`](./LICENSE).
