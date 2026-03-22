# V1 Legacy Path — Sunset Decision Framework

## Executive Summary

This document outlines what would be required to remove the BOT_MODE=v1 legacy path from the codebase. It is a **product/ops decision**, not just a technical cleanup.

**Current State:**
- Default runtime: `orchestrator_v2.py` (multi-persona, full pipeline)
- Legacy runtime: `orchestrator_legacy.py` (single-persona, Bot API)
- Both are functional and tested

---

## What Would Be Removed

### Runtime Components
| Component | Purpose | Impact if Removed |
|-----------|---------|-------------------|
| `src/core/orchestrator_legacy.py` | v1 orchestrator | Breaks `BOT_MODE=v1` |
| `src/contracts/loader.py` | Legacy YAML loading | Breaks `CONTRACT_PATH` usage |
| `config/settings.py` field `contract_path` | Legacy config | Breaks v1 env setup |

### Config/Data Artifacts
| Artifact | Purpose | Impact if Removed |
|----------|---------|-------------------|
| `contracts/korm/persona.yaml` | Legacy contract format | Breaks existing v1 deployments |

### Tests
| Test File | Coverage | Impact if Removed |
|-----------|----------|-------------------|
| `tests/test_integration.py` | E2E v1 tests | Lose v1 test coverage |
| `tests/test_contracts.py` | Contract loading tests | Lose loader tests |

---

## Decision Checklist

### Product/Operations Questions

- [ ] **Do any production deployments use `BOT_MODE=v1`?**
  - Check: Docker containers, systemd services, cron jobs
  - Action: Audit all running instances

- [ ] **Do any users rely on single-persona Bot API mode?**
  - Use case: May be simpler for single-channel deployments
  - Action: Survey current users/admins

- [ ] **Is the v2 multi-persona path stable enough?**
  - Criteria: 30+ days uptime, no critical bugs
  - Action: Review monitoring logs

- [ ] **Is there migration path documentation?**
  - From: v1 single contract YAML
  - To: v2 `personas/<name>/persona.yaml` format
  - Action: Create migration guide

### Technical Blockers

- [ ] **Are all v2 personas migrated from v1 contracts?**
  - Current v1 contracts: `contracts/korm/persona.yaml`
  - Current v2 personas: `personas/kormoved/`, `personas/fitness/`, `personas/smm_blogger/`

- [ ] **Do tests cover the migration path?**
  - Need: Test that validates persona format conversion
  - Current gap: `test_competitor_knowledge.py` uses `_persona_to_contract` adapter

- [ ] **Is monitoring/alerting configured for v2 only?**
  - Check: Health checks, dashboards, alerts
  - Action: Update monitoring to track `orchestrator_v2.py` metrics

---

## Migration Guide (if decided to sunset)

### For Single-Persona Users

```bash
# Before (v1)
export BOT_MODE=v1
export CONTRACT_PATH="contracts/korm/persona.yaml"
python -m src.main

# After (v2)
export BOT_MODE=v2
export PERSONAS_DIR="./personas"
mkdir -p personas/kormoved
cp contracts/korm/persona.yaml personas/kormoved/persona.yaml
# ^ Or use provided migration script
python -m src.main
```

### For Developers

```python
# Before (v1)
from src.core.orchestrator_legacy import SalesBotOrchestrator
from config.settings import AppConfig

config = AppConfig()
config.contract_path = "contracts/korm/persona.yaml"
bot = SalesBotOrchestrator(config)

# After (v2)
from src.core.orchestrator_v2 import SalesBotOrchestratorV2

orchestrator = SalesBotOrchestratorV2(
    personas_dir="./personas",
    memory_dir="./data/memory",
    api_key="...",
)
```

---

## Recommended Timeline (if approved)

| Phase | Duration | Action |
|-------|----------|--------|
| 1. Decision | 1 week | Ops/product sign-off |
| 2. Migration docs | 3 days | Write migration guide + script |
| 3. Announcement | 1 week | Notify users, set deprecation date |
| 4. Code removal | 2 days | Delete legacy files, update tests |
| 5. Validation | 1 week | Monitor v2-only deployments |

---

## Current Inventory of Legacy Usage

### Files to Delete (after sunset)
```
src/core/orchestrator_legacy.py
src/contracts/loader.py
contracts/korm/persona.yaml
```

### Config to Remove
```python
# config/settings.py
contract_path: str  # Remove field
```

### Tests to Update
```
tests/test_integration.py         # Rewrite for v2 or delete
tests/test_contracts.py           # Delete (loader tests)
```

---

## Recommendation

**Do NOT delete v1 path without explicit product/ops approval.**

The legacy path:
- Has working tests
- Has working deployments
- May serve simpler use cases

**Action:**
1. Add deprecation warnings to `orchestrator_legacy.py` startup
2. Document migration path
3. Wait for ops confirmation before removal

---

## Appendix: Current Runtime Inventory (Post-Cleanup)

| Component | Status | Notes |
|-----------|--------|-------|
| `src/main.py` | Canonical | Entrypoint, defaults to v2 |
| `src/core/orchestrator_v2.py` | Canonical | Multi-persona, full pipeline |
| `src/core/persona_manager.py` | Canonical | YAML → Pydantic loading |
| `src/core/orchestrator_legacy.py` | Legacy | BOT_MODE=v1 only |
| `src/core/orchestrator_v3.py` | Experimental | Not wired |
| `src/core/persona_loader_v3.py` | Experimental | Not wired |
| `src/core/decision_gate.py` | Experimental | Part of v3 pipeline |

**Last updated:** After cleanup plan implementation
