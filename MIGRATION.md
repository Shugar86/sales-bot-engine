# Migration Guide — Legacy to Production Architecture

**Date:** March 2026  
**From:** v1/v2/v3 scattered architecture  
**To:** Unified Production Architecture

---

## Summary of Changes

| Aspect | Before | After |
|--------|--------|-------|
| Orchestrator | 3 versions (legacy, v2, v3) | 1 unified (`orchestrator.py`) |
| Entry point | `BOT_MODE=v1/v2` | No mode, single entrypoint |
| Contracts | `contracts/` directory | `personas/` directory only |
| Dedup | JSON file | SQLite |
| Restart | In-orchestrator logic | `lifecycle.py` supervision |
| Health check | Dummy (`sys.exit(0)`) | Real health probes |

---

## Migration Steps

### 1. Environment Variables

**Remove:**
```bash
# No longer needed
export BOT_MODE=v2  # Removed
```

**Keep:**
```bash
# Still required
export OPENROUTER_API_KEY="sk-or-..."
export PERSONAS_DIR="./personas"
export MEMORY_DIR="./data/memory"
export LOG_LEVEL="INFO"

# Per-persona credentials
export KORMOVED_API_ID="..."
export KORMOVED_API_HASH="..."
export KORMOVED_PHONE="..."
```

### 2. File Structure

**Delete these files/directories:**
```bash
# Legacy orchestrators
rm src/core/orchestrator_legacy.py
rm src/core/orchestrator_v3.py
rm src/core/persona_loader_v3.py

# Legacy contracts
rm -rf contracts/
rm src/contracts/loader.py

# Dev artifacts
rm PLAN.md PLAN_v2.md NIGHT_SESSION_LOG.md REPORT.md
docs/V1_SUNSET_DECISION.md
```

**New files:**
```bash
# Core infrastructure
src/core/lifecycle.py      # Task supervision
src/core/retry.py          # Retry/circuit breaker
src/core/health.py         # Health monitoring

# Documentation
ARCHITECTURE.md            # Full architecture docs
MIGRATION.md               # This file
```

### 3. Docker Compose

**Update docker-compose.yml:**

```yaml
# Remove
environment:
  - BOT_MODE=v2  # Remove this line

# Update healthcheck
healthcheck:
  test: ["CMD", "python3", "-m", "src.core.health"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 60s

# Remove volumes
volumes:
  # - ./contracts:/app/contracts:ro  # Remove this
```

### 4. Code Imports

**Update imports in your code:**

```python
# Before
from src.core.orchestrator_v2 import SalesBotOrchestratorV2

# After
from src.core.orchestrator import SalesBotOrchestrator

# Before
orchestrator = SalesBotOrchestratorV2(...)

# After
orchestrator = SalesBotOrchestrator(...)
```

### 5. Test Updates

**Update test imports:**

```python
# Before
from src.core.orchestrator_v2 import SalesBotOrchestratorV2, BotState

# After
from src.core.orchestrator import SalesBotOrchestrator, BotState
```

**Deleted test files:**
- `tests/test_integration.py` (legacy v1 tests)
- `tests/test_contracts.py` (legacy loader tests)
- `tests/test_persona_loader_v3.py` (experimental)

---

## Backward Compatibility

### For Existing Deployments

If you have existing data in `data/memory/processed_messages.json`:

1. The new dedup system will create a new SQLite database alongside it
2. Old JSON data is not automatically migrated (start fresh or handle manually)
3. Telegram offset files will be recreated automatically

### For Existing Personas

Persona YAML files are unchanged. The format remains compatible:
- `personas/<name>/persona.yaml` works as before
- All fields, triggers, examples remain valid

---

## New Capabilities

### 1. Lifecycle Management

```python
from src.core.lifecycle import PersonaSupervisor, SupervisorConfig

supervisor = PersonaSupervisor(
    persona_name="kormoved",
    task_factory=my_task,
    config=SupervisorConfig(max_restarts=5),
)
```

### 2. Retry Policy

```python
from src.core.retry import retry_with_backoff, TELEGRAM_SEND_POLICY

result = await retry_with_backoff(
    my_function,
    policy=TELEGRAM_SEND_POLICY,
    name="my_operation",
)
```

### 3. Health Monitoring

```python
from src.core.health import HealthChecker

checker = HealthChecker()
checker.register("my_check", my_check_function)
await checker.run_checks_and_report()
```

---

## Troubleshooting

### "Module not found" errors

Ensure all new files are present:
```bash
ls src/core/lifecycle.py src/core/retry.py src/core/health.py
```

### Database locked errors

The new SQLite stores use WAL mode and retry logic. If you still see locks:
```bash
# Check if WAL files exist
ls data/memory/*.db-wal

# If stuck, restart the container
docker compose restart
```

### Health check failing

```bash
# Check health file
docker compose exec sales-bot cat /tmp/sales-bot-health.json

# Run health check manually
docker compose exec sales-bot python3 -m src.core.health
```

---

## Rollback Plan

If issues occur:

1. Keep backup of old `orchestrator_v2.py` until migration verified
2. Revert docker-compose.yml environment variables
3. Restart container
4. Check logs: `docker compose logs -f`

---

## Support

For migration issues:
1. Check `ARCHITECTURE.md` for detailed component documentation
2. Review test files for usage examples
3. Check health status via `src.core.health`

---

## Migration Checklist

- [ ] Remove `BOT_MODE` from environment
- [ ] Delete legacy files
- [ ] Update docker-compose.yml healthcheck
- [ ] Verify `src/core/lifecycle.py` exists
- [ ] Verify `src/core/retry.py` exists
- [ ] Verify `src/core/health.py` exists
- [ ] Update imports in custom code
- [ ] Run tests: `pytest tests/`
- [ ] Check health: `python3 -m src.core.health`
- [ ] Verify personas load: check logs
- [ ] Monitor for 24h before considering complete
