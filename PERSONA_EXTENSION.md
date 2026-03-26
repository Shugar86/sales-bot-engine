# Adding a persona

1. Copy an existing folder under `personas/` to `personas/<your_slug>/` and edit `persona.yaml`.
2. Set `session_name` to a stable slug — environment variables for secrets use `SESSION_NAME_UPPERCASE_*` (see `apply_env_overrides_to_persona` in `src/core/persona_manager.py`).
3. Optional: set `memory.entity_profile` to `dog`, `fitness`, or `generic` so SQLite entity extraction matches your domain without code changes.
4. Restart the orchestrator so `discover_personas` picks up the new directory.

CI validates every `personas/*/persona.yaml` via pytest (`tests/test_persona_yaml_schema.py`).
