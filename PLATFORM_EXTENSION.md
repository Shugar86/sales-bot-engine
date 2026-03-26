# Adding a platform

For a new sales persona (YAML + env), see [PERSONA_EXTENSION.md](PERSONA_EXTENSION.md).

Production messaging goes through **one** path: `PlatformAdapter` instances from `src/platforms/registry.py`.

## Minimal contract

1. **Normalize inbound** — map the driver’s native event to `IncomingMessage` (`src/models/message.py`).
2. **Send** — implement `send_reply(msg, text, SendOptions) -> bool`.
3. **Capabilities** — `capabilities() -> PlatformCapabilities` (DM, group reply, reactions, typing, etc.).
4. **Lifecycle** — `run(callback, allowed_chats)` and `stop()` as needed.

Optional: `send_reaction`, `send_typing`, edit/thread helpers if the network supports them.

## Register

In `src/platforms/adapters/your_adapter.py`, provide `async def create(config: PersonaConfig) -> YourAdapter`.

In `src/platforms/registry.py`, inside `_register_builtin_platforms()`:

```python
register_platform("your_platform", "userbot", YourAdapter.create)
```

Use lowercase `platform` and `account_type` keys matching `persona.yaml`.

## Do not

- Add `if platform == "…"` branches in the LangGraph nodes or generator for send path; use the adapter + capabilities instead.
