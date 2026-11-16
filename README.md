# Юридический ИИ-ассистент

Веб-приложение для работы с юридическими документами. Интерфейс на чистых
HTML, CSS и JavaScript раздаётся Nginx; API реализован на FastAPI; PostgreSQL
хранит пользователя, диалоги и сообщения. Запросы интерфейса к `/api/*`
проксируются Nginx на API внутри Docker-сети.

Полное проектное ТЗ — [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Запуск веб-приложения

1. Скопируйте `.env.example` в `.env` в корне репозитория.
2. Замените `<set-me>` на локальные значения `POSTGRES_PASSWORD`,
   `APP_USER_LOGIN` и `APP_USER_PASSWORD`. Не публикуйте `.env` и не помещайте
   настоящие секреты в `.env.example`.
3. Из корня репозитория выполните:

   ```bash
   docker compose up --build
   ```

Откройте интерфейс по адресу `http://localhost:8081`. Swagger API доступен по
адресу `http://localhost:8001/docs`. Порты можно переопределить переменными
`FRONTEND_PORT` и `API_PORT` в `.env`; значения по умолчанию — `8081` и `8001`.

При старте API автоматически создаёт пользователя из `APP_USER_LOGIN` и
`APP_USER_PASSWORD`. Входите в интерфейс с этими учётными данными. Пользователь
и его диалоги хранятся в PostgreSQL.

Остановить контейнеры, не удаляя данные базы:

```bash
docker compose down
```

## Связь интерфейса и API

Nginx раздаёт статику и направляет `/api/*` в сервис `api`. Интерфейс использует
`/api/v1` и bearer-токен из ответа входа.

- `POST /api/v1/auth/login`, `POST /api/v1/auth/logout` — вход и выход;
- `GET/POST /api/v1/conversations` — список и создание диалогов;
- `GET/DELETE /api/v1/conversations/{id}` — просмотр и удаление диалога;
- `GET/POST /api/v1/conversations/{id}/messages` — история и отправка вопроса;
- `GET /api/v1/answers/{answer_id}/sources/{answer_source_id}` — фрагмент
  документа с выделенной цитатой.

Интерфейс загружает историю диалогов с API; избранное хранится локально в
браузере. Поиск и фильтры применяются к загруженной истории. Ответы модели
отображаются в центре, источники — в панели справа.

В интерфейсе также доступны поиск по диалогам и сообщениям, фильтры по периоду
и типам документов, избранное, повтор запроса, форматирование ответов и
просмотр выделенного фрагмента источника.

Вопрос отправляется в `POST /api/v1/conversations/{id}/messages`:

```json
{
  "content": "Вопрос по договору",
  "context": {
    "active_document": {
      "contract_number": "<номер договора>",
      "counterparty": "<контрагент>"
    },
    "event_date": null
  }
}
```

`context` можно не передавать в следующих сообщениях: он сохраняется в
диалоге. Чтобы очистить выбранный документ, передайте
`"active_document": null`. Бэкенд сам добавляет до шести предыдущих сообщений
в запрос к core. Ответ возвращается в JSON-схеме `AnswerResponse` из
`docs/ARCHITECTURE.md`.

### Состояние подключения core

Веб-интерфейс и API запускаются вместе, но обработчик core пока не подключён:
`LegalPipeline.run()` остаётся заглушкой. Поэтому API принимает и сохраняет
вопрос, но сейчас отвечает структурированной ошибкой `503`, а не выдуманным
юридическим ответом.

Для открытия цитат обработчик core должен возвращать `answer` и внутренний
массив `source_locations`. Для каждого источника в нём нужны
`answer_source_id`, UUID документа, версии и пункта, канонический текст и
диапазон выделения (`start_char`, `end_char`). API сохраняет эти данные и
возвращает `before`, `highlight` и `after` по маршруту
`GET /api/v1/answers/{answer_id}/sources/{answer_source_id}`. При подключении
обработчика также задайте `CORPUS_ID` и `INDEX_GENERATION_ID` в `.env`.

Настройка `mock` в `frontend/public/config.js` управляет демо-режимом:
`"auto"` включает его, если API недоступно; `false` отключает демонстрационные
ответы. В демо-режиме используется вход `demo` / `demo`, а ответы берутся из
заготовленных данных `frontend/public/mock-data.js`.
ммм
## Состав проекта

| Путь | Назначение |
| --- | --- |
| `frontend/public/` | HTML, CSS, JavaScript и данные демо-режима |
| `frontend/nginx/default.conf.template` | Раздача интерфейса и прокси `/api/*` |
| `frontend.Dockerfile` | Сборка контейнера интерфейса |
| `Dockerfile` | Сборка контейнера FastAPI |
| `docker-compose.yml` | Совместный запуск PostgreSQL, API и интерфейса |
| `.dockerignore` | Исключения из общего контекста Docker-сборки |
| `backend/` | API входа, диалогов, сообщений и источников |
| `core/` | Контракты и предметное ядро |
| `submission/` | Конкурсная точка входа |
| `docs/` | Архитектура и описание API |

## Предметное ядро и конкурсный запуск

Юридически значимые утверждения должны подтверждаться источниками из текущего
корпуса. Core содержит контракты, поиск, генерацию, проверку и сборку
доказательств; конкурсный сценарий читает `questions.csv` и создаёт
`submission.csv`.

Единый Poetry-проект и зависимости описаны в `.agents`:

```bash
poetry install
poetry run python -m compileall core submission
```

Для конкурсного запуска:

```bash
DATA_DIR=./data poetry run python submission/solution.py
```

## Полный запуск юридического ассистента (RAG)

Нужны только Docker Desktop с Compose и Git. Клонируйте репозиторий и создайте
корневой файл `.env`:

```bash
copy .env.example .env
```

Заполните `POSTGRES_PASSWORD`, `APP_USER_LOGIN`, `APP_USER_PASSWORD` и `LLM_DEV`.
`LLM_DEV` — ключ DeepSeek для `https://matreshkaapi.mooo.com/v1`; ключ не
помещайте в Dockerfile и не публикуйте.

### CPU

```bash
docker compose -f docker-compose.yml \
  -f integration/docker-compose.rag.yml \
  -f integration/docker-compose.models.yml up --build
```

### NVIDIA CUDA

```bash
docker compose -f docker-compose.yml \
  -f integration/docker-compose.rag.yml \
  -f integration/docker-compose.models.yml \
  -f integration/docker-compose.models.cuda.yml up --build
```

При первом запуске Docker скачает BGE-M3 и Qwen3 в постоянные volumes.
Повторный запуск использует уже загруженные веса. В текущем репозитории Qwen
доступен в `Q3_K_M`; если Hugging Face недоступен по TLS, контейнер модели не
запустится до устранения сетевой проблемы.

Откройте интерфейс: <http://localhost:8082>. API: <http://localhost:8001/docs>.
Проверка готовности RAG:

```bash
curl http://localhost:8001/integration/readyz
```

Остановка без удаления данных PostgreSQL и весов:

```bash
docker compose -f docker-compose.yml \
  -f integration/docker-compose.rag.yml \
  -f integration/docker-compose.models.yml down
```

Если `/integration/readyz` показывает `rag_ready=false`, проверьте:

```bash
docker compose ps
docker compose logs api bge-m3 qwen3-reranker
```

Правила для людей и агентов — [`.agents`](.agents) и [`AGENTS.md`](AGENTS.md).
Изменения зависимостей фиксируются в [`CHANGELOG.md`](CHANGELOG.md).
