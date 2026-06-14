# Как участвовать

Sales Bot Engine — персональный R&D-проект. Код открыт для тех, кому выдан доступ, но это не классический open-source продукт.

## Если хочешь предложить изменение

1. **Сначала обсуди** — открой issue или напиши автору. Бизнес-логика, vibe персон и архитектура меняются только после согласования.
2. Сделай форк / ветку от `master` с понятным именем:

   ```bash
   git checkout -b feat/your-feature-name
   ```

3. Пиши коммиты в стиле Conventional Commits:

   ```text
   feat: add anti-spam cooldown per persona
   fix: repair DM routing for Telegram userbot
   docs: update persona extension guide
   refactor: simplify memory facade adapter lookup
   ```

4. Делай **минимальный diff** и придерживайся KISS.
5. Перед PR запусти обязательные проверки:

   ```bash
   pytest -m "not integration" --tb=short
   ruff check .
   ```

6. В описании PR укажи:
   - что изменилось и зачем;
   - как тестировал;
   - риски и ограничения.

## Что точно не примем

- PR без обсуждения, которые меняют vibe персон или продажную логику.
- Коммиты с `.env`, ключами, токенами, сессиями Telegram.
- Крупные рефакторинги "ради красоты" без бизнес-ценности.
- Изменения, ломающие `pytest -m "not integration"` без веской причины.

## Definition of Done для участника

1. `pytest -m "not integration"` проходит.
2. `ruff check .` не выдаёт ошибок.
3. Документация обновлена, если затронуты контракты или архитектура.
4. Коммиты соответствуют Conventional Commits и не содержат секретов.

## Вопросы

Пиши автору репозитория или открывай issue.
