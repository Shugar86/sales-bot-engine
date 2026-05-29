# COCKPIT — sales-bot-engine

## Who this project is (personality, 1-2 sentences)

Это не «бот-продавец», а цифровой двойник опытного человека в нише — с биографией, табу и привычками речи. Проект ведёт себя как senior-коллега: знает предмет, не давит, помнит контекст и продаёт только когда это уместно.

## How it feels (tone, vibe — for each layer/persona)

| Слой | Vibe |
|------|------|
| **Движок (engine)** | Спокойный, надёжный, «лучше помолчу» — без суеты и без fallback-мусора при сбоях LLM |
| **Кормовед (kormoved / Андрей)** | `caring`, `responsible`, `experienced` — бывалый кинолог, коротко, по-деловому, без маркетингового жаргона |
| **Фитнес (fitness)** | Энергичный эксперт-тренер — поддержка, мотивация без токсичного коучинга [детали в `personas/fitness/persona.yaml`] |
| **SMM (smm_blogger / Lera)** | Профессиональный, живой блогер — практичные советы, разговорный тон [детали в `personas/smm_blogger/persona.yaml`] |
| **Групповой чат** | Участник обсуждения — отвечает на «своё», не монологит, соблюдает `max_messages_per_hour` |
| **ЛС (DM)** | Тёплый follow-up из группы — воронка через память и эвристику, без агрессивного closing |

## For whom (3 audiences)

1. **Оператор/R&D (Heisenberg + Doc)** — запуск swarm персон, отладка качества, health и trace.
2. **Конечный пользователь чата** — человек в TG/VK, который должен воспринимать персону как живого эксперта, не как рассылку.
3. **Конфигurator персоны** — тот, кто пишет YAML (vibe, triggers, product) без правок Python-кода.

## Emotions it evokes

- **Доверие** — экспертность и честность вместо «купи сейчас»
- **Спокойствие** — анти-спам, задержки, typing simulation; бота не банят за ощущение живого участника
- **Узнавание** — «это тот же Андрей из чата кинологов», continuity памяти и DM
- **Осторожность у разработчика** — respect к taboos, greeting policy, output validators

## What makes it special (3-5 unique traits)

1. **Philosophy flip:** человек в чате, который *случайно* продаёт — не sales bot с триггерами (ARCHITECTURE_V3.md).
2. **Двухскоростной мозг:** дешёвый router + дорогой generator — экономия без потери качества там, где ответ нужен.
3. **VibeSchema + PromptCompiler + OutputValidators** — persona как контракт, не как промпт-заготовка.
4. **Multi-persona swarm в одном процессе** — изоляция runtime/memory при общем orchestrator.
5. **Degrade gracefully** — Postgres недоступен → legacy path с явным `postgres_degraded`, а не тихий полом.

## Current focus (one paragraph, link to STATE.md for blockers)

Завершён Sprint 4 (production hardening): изоляция, embeddings, quality snapshot, health reporting. Сейчас точка внимания — **consistency рабочего дерева**: uncommitted deletion `src/models/message.py` ломает импорты и противоречит документации; после восстановления модели — smoke с реальными `groups_to_monitor` и прогон integration-тестов. Блокеры и план шагов: [STATE.md](STATE.md).
