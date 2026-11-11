# HTTP API юридического ассистента

Черновики контрактов соответствуют `docs/ARCHITECTURE.md`, раздел 11.
Все маршруты имеют префикс `/api/v1`, кроме `/healthz` и `/readyz`.
Формат — JSON UTF-8; загрузка — `multipart/form-data`.

## Аутентификация

| Метод | Маршрут | Право |
| --- | --- | --- |
| POST | `/auth/login` | неавторизованный |
| POST | `/auth/logout` | авторизованный |
| GET | `/auth/me` | авторизованный |

## Диалоги и запросы

| Метод | Маршрут | Право |
| --- | --- | --- |
| GET | `/conversations` | владелец |
| POST | `/conversations` | авторизованный |
| GET | `/conversations/{id}` | владелец |
| PATCH | `/conversations/{id}` | владелец |
| DELETE | `/conversations/{id}` | владелец |
| GET | `/conversations/{id}/messages` | владелец |
| POST | `/conversations/{id}/messages` | владелец |
| GET | `/requests/{request_id}` | владелец |

## Ответы и источники

| Метод | Маршрут | Право |
| --- | --- | --- |
| GET | `/answers/{answer_id}` | владелец ответа |
| GET | `/answers/{answer_id}/sources/{answer_source_id}` | владелец ответа |
| GET | `/documents/{version_id}` | авторизованный |
| GET | `/documents/{version_id}/clauses/{clause_id}` | авторизованный |

## Администрирование

| Метод | Маршрут |
| --- | --- |
| GET / POST / PATCH | `/admin/users` |
| GET | `/admin/corpora` |
| GET / POST | `/admin/documents` |
| POST | `/admin/documents/{id}/versions` |
| POST | `/admin/index-jobs` |
| GET | `/admin/index-jobs/{id}` |
| GET | `/admin/metrics` |

## Ошибки

Технические коды (см. §11.5) никогда не превращаются в
`legal_status: insufficient_evidence`.
