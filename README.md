# Sales Bot Engine

[![License: Proprietary](https://img.shields.io/badge/License-Proprietary-2c3e50.svg)](./LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776ab.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/Tests-pytest-0a9edc.svg)](./pytest.ini)
[![Code style](https://img.shields.io/badge/Lint-ruff-261c15.svg)](https://docs.astral.sh/ruff/)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ed.svg?logo=docker&logoColor=white)](./docker-compose.yml)

> Мulti-persona оркестратор холодных чат-продаж, который звучит как опытный коллега, а не как рассылка.

```text
   ╭────────────────────────────────────╮
   │  💬  →  🤖  →  ✍️  →  🧠  →  📤   │
   │   chat    router   llm   memory   send  │
   ╰────────────────────────────────────╯
```

## Что это

**Sales Bot Engine** — движок для автоматизации холодных продаж в тематических чатах Telegram и ВКонтакте.

Он запускает несколько независимых персон (кормовед, фитнес-коуч, SMM-специалист и др.), каждая из которых слушает свои чаты, выбирает момент для реплики и отвечает так, будто в чате сидит живой эксперт. Если человек заинтересован и переходит в личные сообщения — бот мягко продолжает разговор и помогает подобрать продукт.

Ключевой принцип: **сначала польза и доверие, продажа — следствие диалога.**

## Возможности

- ✨ **Multi-persona swarm** — каждая персона работает в изолированном runtime со своей памятью и rate limits.
- 🧠 **LangGraph-пайплайн** с персистентными чекпоинтами в PostgreSQL.
- 🛡️ **Graceful degrade** — если PostgreSQL недоступен, движок переключается на legacy-путь без остановки.
- 📝 **YAML-контракт персоны** — характер, табу, триггеры, примеры для тьюринг-теста и анти-спам в одном файле.
- ⚡ **Fast Router + Slow Generator** — быстрая модель решает "отвечать или нет", медленная пишет текст.
- 🔌 **PlatformAdapter registry** — новая платформа добавляется одним модулем, без условных ветвлений в графе.
- 🎭 **Human-like behavior** — typing simulation, вариативные задержки, leave-on-read, emoji-реакции.
- 📊 **Наблюдаемость** — `message_trace` на каждое сообщение, health snapshot и CLI-скрипты.

## Быстрый старт

```bash
# 1. Клонировать репозиторий
git clone git@github.com:Shugar86/sales-bot-engine.git
cd sales-bot-engine

# 2. Подготовить окружение
cp .env.example .env
# Отредактируй .env: OPENROUTER_API_KEY, DATABASE_URL, Telegram API и телефоны персон.

# 3. Установить зависимости
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 4. Запустить оркестратор
python -m src.main
```

### Production-деплой через Docker

```bash
cp .env.example .env
# заполнить .env реальными значениями
docker compose up -d --build
```

### Проверка здоровья

```bash
python scripts/health_check.py
python scripts/quality_snapshot.py
```

## Архитектура / стек

| Область | Технология | Назначение |
|---------|------------|------------|
| Язык | Python 3.11+ | Runtime и бизнес-логика |
| LLM-роутинг | Gemini Flash 2.0 через OpenRouter | Решение "отвечать / игнорировать" |
| LLM-генерация | Claude Sonnet 3.5 через OpenRouter | Написание человеческих реплик |
| Пайплайн | LangGraph + `langgraph-checkpoint-postgres` | Состояние и оркестрация |
| Telegram | Telethon (userbot) + Telegram Bot API | Получение и отправка сообщений |
| ВКонтакте | VK API (extension point) | Адаптер для VK |
| Память | PostgreSQL + pgvector, SQLite per persona | Дедупликация, legacy, degrade |
| Эмбеддинги | `sentence-transformers` (`deepvk/USER-bge-m3`) | Семантический поиск |
| Конфиг | PyYAML + Pydantic v2 | Контракты и валидация |
| CI / тесты | GitHub Actions, pytest, ruff | Качество кода |

### Пайплайн обработки сообщения

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
│   ├── monitors/              # Низкоуровневые драйверы платформ
│   ├── memory/                # MemoryFacade, Supabase, embeddings, degraded mode
│   ├── responders/            # Composer, валидаторы, humanizer
│   └── main.py                # Точка входа
├── config/                    # Загрузка конфигурации окружения
├── scripts/                   # health_check.py, quality_snapshot.py
├── tests/                     # Unit + integration тесты
├── supabase/migrations/       # Схема PostgreSQL
├── docker-compose.yml         # Production-деплой
└── Dockerfile
```

## Примеры

### Фрагмент персоны "Кормовед"

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
  taboos: ["политика", "религия", "маркетинговый жаргон", "простыни текста"]

behavior:
  on_greeting: "Здравствуйте. Какая собака?"
  on_dm: "Вспомни из какого чата человек. Болтай как в группе."

anti_spam:
  min_delay_between_messages: 30
  leave_on_read: 0.35
  typing_simulation: true
```

### Пример диалога

```text
Пользователь: Щенок хаски, 3 месяца, что давать?
Бот: Хаски в 3 месяца — расти будет быстро, суставы нагружать.
      Бери корм для щенков крупных пород, с глюкозамином.
      И не перекармливай — хаски склонны к дисплазии, если лишний вес.
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
- [`AGENTS.md`](./AGENTS.md) — контракт для агентов, работающих с репо

## Статус

Проект в активной R&D-разработке. API, форматы YAML и внутренние контракты могут меняться — см. [`CHANGELOG.md`](./CHANGELOG.md).

## Лицензия

Проприетарная, все права защищены — см. [`LICENSE`](./LICENSE) © 2026 Shugar86.
