# Sales Bot Engine v2 — Multi-Platform Userbot

**Статус:** 🔄 В работе (refactor с Bot API → Userbot)
**Дата:** 2026-03-18

## Что есть (v1)
- 2840 строк Python, 45 тестов
- Bot API (getUpdates) — надо добавлять бота в группы
- Один контракт (Кормовед)
- Fast Router + Slow Generator + YAML contracts + Memory

## Что нужно (v2)

### Архитектура: Multi-Persona Userbot

```
┌──────────────────────────────────────────────┐
│                 Orchestrator                  │
│  (управляет N персонами одновременно)         │
├──────────┬──────────┬──────────┬─────────────┤
│ Persona 1│ Persona 2│ Persona 3│  ...        │
│ Кормовед │ Фитнес   │ SMM      │             │
├──────────┴──────────┴──────────┴─────────────┤
│              Monitors Layer                   │
├──────────┬──────────┬──────────┬─────────────┤
│ Telethon │ VK API   │ Пикабу   │ Instagram*  │
│ (TG)     │          │ (scrape) │ (future)    │
└──────────┴──────────┴──────────┴─────────────┘
```

### Каждая персона = YAML контракт + аккаунт

```yaml
persona:
  name: "Кормовед"
  account_type: "userbot"  # или "bot"
  platform: "telegram"
  phone: "+79001234567"     # для userbot
  # или
  bot_token: "123:ABC"      # для bot
  
  groups_to_monitor:
    - "-100123456789"  # Группа овчарок
    - "-100987654321"  # Группа кинологов
    
  personality: |
    Ты опытный кинолог, 10 лет со среднеазиатскими овчарками.
    Говоришь просто, без пафоса. Иногда кидаешь фотки своих собак.
    
  product:
    name: "Премиум корм для служебных собак"
    price: "3500₽ за 15кг"
    link: "https://example.com"
    
  triggers:
    respond_when:
      - keywords: ["корм", "питание", "чем кормите", "собака не ест"]
      - topics: ["здоровье собаки", "аллергия", "рацион"]
    ignore_when:
      - contains: ["продам", "куплю", "цена"]
      - from_bot: true
      
  conversation_flow:
    group_mode:
      max_messages_per_hour: 3
      probability_to_respond: 0.3  # отвечаем на 30% релевантных
      style: "экспертный, ненавязчивый, иногда просто 'мой тоже так'"
      
    dm_mode:
      greeting: "Привет! Видел твой вопрос в чате, могу поделиться опытом"
      funnel:
        - step: "помочь с вопросом"
        - step: "упомянуть что сам кормлю определённым"
        - step: "если интересно — скинуть ссылку"
        
  anti_spam:
    min_delay_between_messages: 120   # секунд
    max_delay_between_messages: 600
    typing_simulation: true
    random_typos: false  # или true для реализма
```

### Платформы

#### Telegram (Telethon userbot)
- `telethon` — MTProto API, полноценный юзербот
- Читает ВСЕ сообщения в группах (не надо добавлять)
- Отправляет как обычный юзер
- Риск: аккаунт могут забанить (mitigate: антидетект, прокси, delays)

#### VK (vk_api / vk-async)
- `vk_api` — Python библиотека для VK API
- Long Poll для мониторинга сообщений в группах
- Отправка сообщений в беседы и ЛС
- Меньше риска бана чем в TG

#### Пикабу (web scraping)
- `httpx` + `beautifulsoup4` / `playwright`
- Мониторинг новых постов в тегах
- Комментирование (через авторизацию)
- Самый ненадёжный канал

### Модели

| Роль | Модель | Когда |
|------|--------|-------|
| Router | Gemini Flash | Каждое сообщение (часто, дёшево) |
| Generator | Claude Sonnet / Hunter-alpha | Когда решено отвечать (реже) |
| DM Dialog | Claude Sonnet | Личные диалоги (качество важно) |

### Файловая структура (v2)

```
sales-bot-engine/
├── personas/
│   ├── kormoved/
│   │   ├── persona.yaml
│   │   ├── knowledge.md      # что знает про продукт
│   │   └── examples.md       # примеры хороших ответов
│   ├── fitness/
│   │   └── persona.yaml
│   └── smm/
│       └── persona.yaml
├── sessions/                 # Telethon session файлы
│   ├── kormoved.session
│   └── fitness.session
├── src/
│   ├── core/
│   │   ├── orchestrator.py   # управляет N персонами
│   │   ├── router.py         # Fast Router (Gemini)
│   │   └── persona_manager.py # загрузка/перезагрузка персон
│   ├── monitors/
│   │   ├── telegram_userbot.py  # Telethon монитор
│   │   ├── vk_monitor.py        # VK Long Poll
│   │   └── pikabu_monitor.py    # Пикабу scraper
│   ├── responders/
│   │   ├── generator.py       # генерация ответов
│   │   └── anti_spam.py       # rate limiter
│   ├── memory/
│   │   └── user_memory.py     # память по юзерам
│   └── utils/
│       ├── llm_client.py
│       ├── logger.py
│       └── dedup.py
├── data/
│   ├── memory/
│   └── logs/
└── tests/
```

## TODO

### Phase 1: Telegram Userbot
- [ ] Добавить Telethon в зависимости
- [ ] Переписать telegram_monitor.py → telegram_userbot.py
- [ ] Поддержка phone auth + session файлов
- [ ] Мониторинг групп (NewMessage event)
- [ ] Отправка сообщений как юзер
- [ ] DM handler (личные диалоги)

### Phase 2: Multi-Persona
- [ ] Persona Manager — загрузка всех YAML из personas/
- [ ] Orchestrator — запуск N персон параллельно
- [ ] Каждая персона = свой Telethon client + свой контракт

### Phase 3: VK Monitor
- [ ] VK API мониторинг
- [ ] Long Poll events
- [ ] Отправка сообщений

### Phase 4: Пикабу
- [ ] Scraper для новых постов
- [ ] Комментирование (auth)

### Phase 5: Anti-Detect
- [ ] Прокси-ротация
- [ ] Human-like delays (typing simulation)
- [ ] Случайные паттерны активности
- [ ] Мониторинг банов
