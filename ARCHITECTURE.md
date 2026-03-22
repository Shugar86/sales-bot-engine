# Sales Bot Engine — Architecture Documentation

**Version:** Production Unified Architecture  
**Date:** March 2026  
**Status:** Active Development

---

## Overview

The Sales Bot Engine is a multi-persona sales automation system that monitors Telegram/VK chats, detects relevant conversations, and responds with natural, persona-specific messages. It is designed for R&D internal use, not as a SaaS platform.

### Key Characteristics

- **Telegram-first**: Primary platform is Telegram (userbot + Bot API)
- **Multi-persona**: Each persona runs in isolation with its own runtime, memory, and rate limits
- **Human-like behavior**: Typing simulation, leave-on-read, emoji reactions, variable delays
- **Turing test optimized**: Responses trained to avoid bot-like patterns

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Entry Point                              │
│                        src/main.py                               │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │  Config Validation → Signal Handlers → Orchestrator      │ │
│  └──────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Orchestrator                               │
│                   src/core/orchestrator.py                       │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │  • Loads personas from personas/ directory               │ │
│  │  • Creates PersonaRuntime for each                       │ │
│  │  • Registers with LifecycleManager                       │ │
│  │  • Handles graceful shutdown                               │ │
│  └──────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Lifecycle Manager                            │
│                   src/core/lifecycle.py                        │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │  • PersonaSupervisor per persona                         │ │
│  │  • Automatic restart with exponential backoff             │ │
│  │  • Health tracking (last_alive, restart_count)            │ │
│  │  • Graceful shutdown coordination                         │ │
│  └──────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Per-Persona Runtime                           │
│              (Pipeline: 10 stages)                              │
├─────────────────────────────────────────────────────────────────┤
│  Monitor → IncomingMessage → Dedup → Preprocess → Anaphora →    │
│  Router → (Leave-on-Read/Emoji) → Generator → OutputValidator → │
│  AntiSpam → Send → Memory                                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## Module Responsibilities

### Core Modules

| Module | Responsibility | Key Classes |
|--------|---------------|-------------|
| `orchestrator.py` | Main coordination, persona loading, pipeline execution | `SalesBotOrchestrator`, `PersonaRuntime`, `BotState` |
| `lifecycle.py` | Task supervision, restart policy, health tracking | `PersonaSupervisor`, `LifecycleManager`, `SupervisorConfig` |
| `persona_manager.py` | YAML loading, config validation | `PersonaConfig`, `discover_personas()` |
| `router.py` | Fast routing decision (respond/ignore) | `MessageRouter`, `Decision` |
| `generator.py` | Response generation with examples | `ResponseGenerator` |
| `retry.py` | Centralized retry/backoff/circuit breaker | `RetryManager`, `CircuitBreaker`, `RetryPolicy` |
| `health.py` | Health probes and status reporting | `HealthChecker`, `HealthReporter` |

### Monitor Modules

| Module | Responsibility | Key Features |
|--------|---------------|--------------|
| `telegram_userbot.py` | Telethon-based monitoring | Reconnection, retry, graceful shutdown |
| `telegram_monitor.py` | Bot API long polling | Offset persistence, 429 handling |
| `vk_monitor.py` | VK API monitoring | Extension point (sync API) |
| `anti_spam.py` | Rate limiting, delays | Per-persona limits, typing simulation |

### Storage Modules

| Module | Responsibility | Storage |
|--------|---------------|---------|
| `dedup.py` | Message deduplication | SQLite (WAL mode) |
| `user_memory.py` | User context, funnel tracking | SQLite (WAL mode) |

---

## Data Flow

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Telegram   │────▶│   Monitor    │────▶│  Dedup Check │
└──────────────┘     └──────────────┘     └──────┬───────┘
                                                │
                                                ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Memory     │◀────│   Memory     │◀────│  Preprocess  │
│   Update     │     │   Lookup     │     │  (shortcuts) │
└──────────────┘     └──────────────┘     └──────┬───────┘
                                                │
                                                ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│    Send      │◀────│   AntiSpam   │◀────│  Generator   │
│  Response    │     │   (delay)    │     │  (Claude)    │
└──────────────┘     └──────────────┘     └──────┬───────┘
                                                  │
                                                  ▼
                                           ┌──────────────┐
                                           │    Router    │
                                           │  (Gemini)    │
                                           └──────────────┘
```

---

## Persona Configuration

Each persona is a directory in `personas/` with a `persona.yaml` file:

```yaml
persona:
  name: "Андрей"
  platform: "telegram"
  account_type: "userbot"
  session_name: "kormoved"
  personality: "Бывший кинолог-инструктор..."

  vibe:
    role: "Консультант по кормлению"
    taboos: ["политика", "религия"]

  behavior:
    on_greeting: "Здравствуйте. Какая собака?"
    on_dm: "Вспомни из какого чата человек..."

  triggers:
    respond_when:
      - keywords: ["корм", "собака"]
        topics: ["кормление"]
        probability: 0.8

  anti_spam:
    min_delay_between_messages: 30
    leave_on_read: 0.35
    typing_simulation: true

  response_examples:
    - trigger: "Дорого"
      bad_response: "Наши цены конкурентные..."
      good_response: "Да, не дёшево. Но посчитай..."
```

---

## Reliability Features

### Restart Supervision

- Max 5 restarts per persona
- Exponential backoff: 10s, 20s, 40s, 80s, 160s
- Jitter: ±25% to prevent thundering herd
- Cancellation safety: `CancelledError` propagates without restart

### Telegram Reliability

- Userbot: Reconnection loop with backoff
- Userbot: `stop()` properly breaks `run_until_disconnected()`
- Bot API: Offset persisted to SQLite (survives restart)
- Send operations: 3 retries with exponential backoff
- 429 handling: Respects `Retry-After` header

### LLM Reliability

- Response structure validation (no KeyError on malformed JSON)
- Circuit breaker: Opens after 5 consecutive failures
- Retry-After header support for 429
- Per-call timeout override (router=fast, generator=slow)

### State Persistence

- Deduplication: SQLite with WAL mode
- Memory: SQLite with WAL mode
- Offset persistence: JSON file (atomic write)

---

## Health Monitoring

### Health Checks

- `llm_api`: Ping OpenRouter API
- `memory_writable`: Test write to memory directory
- `personas`: Verify personas directory and configs

### Health File

Written to `/tmp/sales-bot-health.json` every 30s:

```json
{
  "status": "healthy",
  "timestamp": 1700000000,
  "checks": {
    "llm_api": {"status": "healthy", "latency_ms": 150},
    "memory_writable": {"status": "healthy"},
    "personas": {"status": "healthy", "count": 3}
  }
}
```

---

## Deployment

### Docker Compose

```bash
# Setup
cp .env.example .env
# Edit .env with your keys

# Run
docker compose up -d
docker compose logs -f
```

### Health Check

```bash
# Manual health check
docker compose exec sales-bot python3 -m src.core.health

# Or read health file
cat /tmp/sales-bot-health.json
```

---

## Security Considerations

- API keys in environment variables only
- Session files mounted as volumes (not in images)
- No secrets in logs (tokens redacted)
- Read-only persona configs
- No external webhooks (poll only)

---

## Known Limitations

1. **Telethon auth**: Requires interactive phone code on first run
2. **VK monitor**: Sync API in async wrapper (not fully async)
3. **Single process**: One crash kills all personas (mitigated by Docker restart)
4. **No persistent queue**: Message loss possible between receive and send
5. **Memory DB contention**: All personas share one SQLite file

---

## Future Extension Points

- `decision_gate.py`: Enhanced decision logic (currently experimental)
- `vibe_checker.py`: Persona vibe matching (currently experimental)
- `context_reader.py`: Chat context analysis (in use)
- `health.py`: Additional health checks

---

## Related Documents

- `MIGRATION.md`: Migration guide from legacy versions
- `README.md`: Quick start and overview
- `docs/`: Additional documentation
