# PaperFlow Hybrid Local Hub Architecture

## 1. Цель

PaperFlow должен выглядеть для учителя как обычный веб-сервис, но персональные
данные учеников не должны передаваться в облачный контур. Поэтому система
разделена на два независимых trust boundary.

```text
Yandex Cloud / CDN                     Школьный или персональный контур
┌──────────────────────┐              ┌────────────────────────────────┐
│ PaperFlow Web        │  HTTPS/PNA   │ PaperFlow Hub                  │
│ HTML, JS, CSS, WASM  │ ───────────► │ FastAPI, OpenCV, QR, OCR       │
│ без данных учеников  │  pairing     │ SQLite, изображения, backups   │
└──────────────────────┘              └────────────────────────────────┘
```

Облачная часть является delivery/control plane. Локальный Hub является data
plane и единственным владельцем ученических данных.

## 2. Непересекаемая граница данных

В cloud control plane разрешены только строго типизированные контракты из
`backend/app/cloud/contracts.py`:

- installation ID;
- версия Hub и протокола;
- ОС и архитектура;
- канал обновлений;
- статус лицензии в виде хеша;
- фиксированные технические события и коды ошибок.

Запрещены:

- ФИО, идентификаторы и списки учеников;
- классы и названия учебных групп;
- задания, ответы и оценки;
- изображения, QR payload и OCR-текст;
- комментарии учителя;
- база, backup и diagnostics bundle.

В cloud-контрактах намеренно отсутствует произвольный `payload: dict`. Новое
поле нельзя добавить незаметно: Pydantic-модели используют `extra="forbid"`.

## 3. Discovery и сетевой транспорт

PaperFlow Web рассматривает как допустимые Hub-адреса только:

- `localhost`, `127.0.0.0/8`, `::1`;
- RFC1918: `10/8`, `172.16/12`, `192.168/16`;
- имена `.local`;
- точные имена из `VITE_PAPERFLOW_ALLOWED_HUB_HOSTS`.

Публичный hostname нельзя установить как Hub API. Это защита не только на уровне
документации, но и в клиентском коде `frontend/src/hub/runtime.ts`.

Production-соединение cloud → local должно использовать HTTPS с сертификатом,
которому доверяет ОС/браузер. Установщик Hub должен:

1. создать уникальный локальный ключ;
2. выпустить сертификат для loopback/local DNS;
3. безопасно добавить локальный CA или leaf certificate в доверенное хранилище;
4. запустить Hub на `127.0.0.1:17841`;
5. не слушать внешние интерфейсы в personal mode.

Для School Hub сертификат выпускается для внутреннего DNS-имени, например
`paperflow.school.local`, а firewall допускает только школьную сеть.

## 4. Origin и pairing

Hub принимает внешний браузер только когда его точный Origin присутствует в
`PAPERFLOW_HUB_ALLOWED_ORIGINS`.

Сопряжение:

1. Web вызывает `POST /api/hub/pair/start`.
2. Hub создаёт challenge и шестизначный одноразовый код.
3. Учитель открывает локальную страницу `pair/display/...` или видит код в tray.
4. Web отправляет код в `POST /api/hub/pair/confirm`.
5. Hub выдаёт случайный token.
6. На диске хранится только SHA-256 token, а не исходный token.
7. Token привязан к Origin и workspace.

HTTP использует `X-PaperFlow-Hub-Token`. WebSocket использует `hub_token` в query,
поскольку браузерный WebSocket API не позволяет задать произвольный заголовок.
Для URL с token действует глобальный `Referrer-Policy: no-referrer`.

## 5. Workspace contract

Каждый запрос получает `HubRequestContext`:

```text
installation_id
deployment_mode
workspace_id
actor_id
role
client_id
origin
authenticated
```

Frontend всегда передаёт `X-PaperFlow-Workspace`. В personal mode допустим только
workspace `personal`. Это позволяет сейчас сохранить простую локальную модель,
но уже не фиксировать архитектуру на глобальной безымянной базе.

## 6. Переход к School Hub

School mode запрещён конфигурационным guard, пока не включена tenant-scoped
схема. Следующая миграция должна быть выполнена до снятия guard.

### 6.1 Новые сущности

```text
organizations
workspaces
hub_users
workspace_memberships
roles
api_clients
audit_events
```

### 6.2 Tenant scope

Следующие таблицы должны получить обязательный `workspace_id`:

```text
class_groups
students
tasks
form_templates
scan_sessions
scanned_sheets
camera_profiles
session_presets
hardware_events
```

Дочерние OCR/review/log таблицы получают workspace через sheet/session, но для
защиты и индексации допускается денормализованный `workspace_id`.

### 6.3 Порядок миграции

1. Создать organization и workspace для существующей установки.
2. Добавить nullable `workspace_id`.
3. Заполнить его значением personal workspace.
4. Добавить составные foreign keys и индексы.
5. Перевести repository/query layer на обязательный `HubRequestContext`.
6. Проверить отсутствие запросов без tenant predicate.
7. Сделать поля NOT NULL.
8. Добавить пользователей, membership и role checks.
9. Только после этого включить `PAPERFLOW_HUB_SCHOOL_TENANCY_ENABLED=true`.

### 6.4 Авторизация School Hub

Pairing token не является полноценной школьной учётной записью. Для School Hub
нужны:

- локальные пользователи или интеграция с школьным IdP;
- Argon2id для паролей;
- короткоживущая access-сессия и refresh rotation;
- роли owner/admin/teacher/viewer;
- membership на workspace;
- audit log операций с учениками, оценками, экспортом и удалением;
- блокировка и отзыв устройств.

Контракт `HubRequestContext` уже совместим с этой моделью.

## 7. Privacy и PWA

Service Worker кэширует только статические same-origin ресурсы интерфейса.
Запросы `/api/*` и любой cross-origin Hub traffic не перехватываются и не
попадают в Cache Storage.

Локальный backup создаётся SQLite Backup API. Credential store Hub хранится
отдельно и не переносится вместе с базой: восстановление backup не клонирует
доверенные браузерные сессии.

## 8. Deployment в Yandex Cloud

Для первого cloud-релиза достаточно:

- Object Storage для статического `frontend/dist`;
- CDN и собственный HTTPS-домен;
- immutable cache для hashed assets;
- `index.html` и manifest с коротким cache;
- отсутствие server-side API с ученическими DTO.

Опциональный control API допускается только для контрактов из `app/cloud`.
Секреты control plane должны находиться в Lockbox; Hub не должен хранить
облачные service-account keys, дающие доступ к инфраструктуре.

## 9. Следующие этапы

### 0.3 — выполненный фундамент

- внешний Hub discovery;
- private-network URL guard;
- CORS/PNA boundary;
- Origin-bound pairing;
- workspace request contract;
- metadata-only cloud DTO;
- PWA shell без кэширования данных;
- CI для backend и frontend.

### 0.4 — production Personal Hub

- Windows tray application;
- signed installer;
- автозапуск;
- locally trusted TLS;
- automatic update with signed manifests;
- installer/uninstaller data-preservation policy;
- UI управления подключёнными браузерами.

### 0.5 — School Hub persistence

- organizations/workspaces/users/memberships;
- workspace migration всех данных;
- repository layer с mandatory scope;
- audit log;
- role-based access control;
- central backup policy.

### 0.6 — School deployment

- LAN discovery/internal DNS;
- multi-teacher UI;
- admin console;
- scheduled encrypted backups;
- update rings;
- monitoring без содержимого ученических данных.
