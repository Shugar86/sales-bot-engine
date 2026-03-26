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
- **Plain-text generation**: Slow model is prompted for user-facing message text (not JSON-first); post-processing strips legacy JSON artifacts when models misbehave.
- **Persona drift guardrails**: `persona_yaml_validate.py` enforces semantic YAML rules in CI via pytest (see `tests/test_persona_yaml_schema.py`).

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
│  │  • create_adapter(config) → PlatformAdapter (registry)    │ │
│  │  • Registers with LifecycleManager                       │ │
│  │  • Handles graceful shutdown (adapter.stop())             │ │
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
│   LangGraph when DATABASE_URL set; legacy path only if unset    │
├─────────────────────────────────────────────────────────────────┤
│  PlatformAdapter.run → IncomingMessage → LangGraph:             │
│  Dedup → Preprocess → parallel_retrieval → Route →               │
│  AntiSpam (incl. per-user DM inbound burst) → Generate →         │
│  Validate → Send (adapter) → Memory (funnel heuristic)           │
└─────────────────────────────────────────────────────────────────┘
```

---

## Module Responsibilities

### Core Modules

| Module | Responsibility | Key Classes |
|--------|---------------|-------------|
| `orchestrator.py` | Main coordination, persona loading, LangGraph invoke; one JSON **message_trace** per handled message (`path`, `nodes`, `latency_ms`, `llm_error`, …) | `SalesBotOrchestrator`, `PersonaRuntime` (`adapter`), `BotState` |
| `lifecycle.py` | Task supervision, restart policy, health tracking | `PersonaSupervisor`, `LifecycleManager`, `SupervisorConfig` |
| `persona_manager.py` | YAML loading; `discover_personas`; optional `memory.entity_profile` for extractors | `PersonaConfig`, `discover_personas()` |
| `funnel_heuristic.py` | DM funnel stage suggestion from current stage + user text (shared SQLite/Postgres semantics) | `suggest_funnel_stage()` |
| `persona_yaml_validate.py` | Static + **semantic** persona YAML checks (errors/warnings) | `validate_all_personas_under()`, `assert_persona_yaml_file_valid()` |
| `router.py` | Fast routing; invalid LLM decision → `RouteResult.parse_failed` (not silent ignore) | `MessageRouter`, `Decision` |
| `generator.py` | Response generation; on LLM failure/timeout returns **`None`** (no random fallback text) | `ResponseGenerator` |
| `retry.py` | Centralized retry/backoff/circuit breaker | `RetryManager`, `CircuitBreaker`, `RetryPolicy` |
| `health.py` | Health probes and status reporting | `HealthChecker`, `HealthReporter` |

### Monitor Modules

| Module | Responsibility | Key Features |
|--------|---------------|--------------|
| `telegram_userbot.py` | Telethon-based monitoring | Reconnection, retry, graceful shutdown |
| `telegram_monitor.py` | Bot API long polling | Offset persistence, 429 handling |
| `vk_monitor.py` | VK API monitoring | Extension point (sync API) |
| `anti_spam.py` | Rate limiting, delays, outgoing send accounting | `RateLimiter`; per-user **DM inbound burst** is enforced in `graph/nodes.py` + persisted streak in memory (`users.extra.dm_inbound_streak`), threshold from `PersonaConfig.anti_spam.dm_max_inbound_burst_without_bot_reply` |

### Platform adapters (`src/platforms/`)

| Module | Responsibility |
|--------|----------------|
| `protocol.py` | `PlatformAdapter` — inbound `run()`, `send_reply`, `send_reaction`, `send_typing`, capabilities |
| `registry.py` | `create_adapter(PersonaConfig)` keyed by `(platform, account_type)` |
| `adapters/*` | Thin facades over `src/monitors/*` drivers (Telegram userbot/bot, VK) |

The orchestrator and LangGraph nodes depend only on `PlatformAdapter`, not on Telethon/VK APIs. Adding a network means a new adapter module plus a registry entry.

### Storage Modules

| Module | Responsibility | Storage |
|--------|---------------|---------|
| `dedup.py` | Message deduplication | SQLite (WAL mode) |
| `user_memory.py` | User context, funnel, entity extraction by `memory.entity_profile` | SQLite (`users.extra` JSON) |
| `supabase_memory.py` | Async user/DM/group memory, semantic search | PostgreSQL + optional pgvector |
| `memory_facade.py` | Unified API for graph: context, `get_dm_transcript_for_prompt`, streak helpers, embeddings | Delegates to `SupabaseMemory` |

**DM prompt context:** `generate_node` loads `funnel_stage` via `get_funnel_stage`, recommendations via `get_recommendations`, and a chronological thread tail via `get_dm_transcript_for_prompt` (not only the condensed profile block). After a reply, `memory_node` records the exchange and updates funnel stage using `funnel_heuristic.suggest_funnel_stage` (not the LLM’s `stage` field).

---

## Data Flow

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Telegram   │────▶│PlatformAdapter│───▶│  Dedup Check │
│   / VK / …   │     │  .run()      │    │  (memory)    │
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
│adapter.send  │     │   (delay)    │     │  (Claude)    │
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

  anti_spam:
    min_delay_between_messages: 30
    leave_on_read: 0.35
    typing_simulation: true
    dm_max_inbound_burst_without_bot_reply: 3  # optional; consecutive inbound DMs without bot send

  memory:
    entity_profile: generic   # optional: dog | fitness | generic (SQLite entity extractor)

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

- **Router:** malformed or unknown `decision` from the fast model sets `parse_failed=True` on `RouteResult` (logged); not treated as a silent “ignore”.
- **Generator:** LLM errors, timeouts, or `success=False` yield **`None`**; the graph does not send a placeholder reply. `generate_node` sets `llm_failed`; orchestrator trace includes `llm_error` when applicable.
- **Legacy path** (no `DATABASE_URL`): same “no junk on LLM failure” behavior for the minimal route → generate → send flow.
- Circuit breaker and retries remain on the HTTP client where configured; per-call timeouts differ (router=fast, generator=slow).

### State Persistence

- **LangGraph checkpoints:** PostgreSQL via `DATABASE_URL` (`langgraph-checkpoint-postgres`; tables `checkpoints` / `writes` — see `supabase/migrations/002_langgraph_checkpoints.sql`).
- **Application memory:** `MemoryFacade` → Supabase PostgreSQL + pgvector (`001_initial_schema.sql`); embeddings for semantic search.
- **Legacy / migration:** per-persona JSON dedup files may still exist alongside Supabase `processed_messages`.
- **Telegram Bot API:** update offset persisted to JSON file (atomic write).

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

### Orchestrator status

`SalesBotOrchestrator.get_status()` returns per persona: `platform` and `account_type` from YAML, `platform_key` from `PlatformAdapter` when the inbound loop has started (otherwise `null`), plus stats and lifecycle supervisor fields.

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
# CLI snapshot (personas, optional Postgres ping, optional LLM probe) — exit 0/1
python scripts/health_check.py
python scripts/health_check.py --format table

# In-container module health (library probes)
docker compose exec sales-bot python3 -m src.core.health

# Optional periodic JSON snapshot (if the process writes it)
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
5. **Memory / DB**: With Supabase, tuning pool size and indexes (pgvector) matters; legacy docs may still mention single SQLite for older deployments.

---

## Future Extension Points

- `src/platforms/registry.py`: Register new `(platform, account_type)` factories (Reddit, X, etc.).
- `decision_gate.py`: Enhanced decision logic (currently experimental)
- `vibe_checker.py`: Persona vibe matching (currently experimental)
- `context_reader.py`: Chat context analysis (in use)
- `health.py`: Additional health checks; optional exposure of `platform_key` from status API

---

## Related Documents

- `MIGRATION.md`: Migration guide from legacy versions
- `README.md`: Quick start and overview
- `PERSONA_EXTENSION.md`: Adding a persona (folder + YAML + env prefix)
- `PLATFORM_EXTENSION.md`: Adding a messaging platform adapter
- `docs/`: Additional documentation
