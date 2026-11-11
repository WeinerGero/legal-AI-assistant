# Правила для агентов — краткая шпаргалка

Полные правила: [`.agents`](../.agents).

## Кто где работает

| Участник | Каталоги |
| --- | --- |
| Участник 1 | `core/`, `submission/` |
| Участник 2 | `backend/`, `frontend/`, `worker/` |
| Участник 3 | `tests/` |

Чужие каталоги не изменяются. Общие файлы (`README.md`, `pyproject.toml`,
`poetry.toml`, `.gitignore`, `compose.yaml`, `docs/ARCHITECTURE.md`) —
только по явной команде пользователя.

## Зависимости

Один Poetry-проект: `pyproject.toml` + `poetry.lock`. Существующие
зависимости заморожены — версии не меняются, новые пакеты добавляются только
по явной команде пользователя и с записью в `CHANGELOG.md`.

```bash
poetry install              # установка окружения
poetry run python -m compileall core backend frontend worker submission
poetry run pytest tests     # прогон тестов
```

## Секреты

`.env` не читается агентом. Образец переменных — `.env.example`. Значения
ключей не попадают в код, логи, тесты и сообщения коммитов.

## Коммиты

Один коммит на логическую единицу, не реже раза в 30–60 минут.
Формат: `<участник>: <область> — <что сделано>`.

Перед коммитом:

```bash
git status
git diff --cached --stat
```
