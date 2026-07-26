# Развёртывание PaperFlow Web на Vercel

Vercel используется только для статического браузерного интерфейса. FastAPI,
OpenCV, OCR, SQLite, изображения работ, ФИО, ответы и оценки остаются внутри
локального PaperFlow Hub.

## Что уже подготовлено

В `frontend` находятся:

- `vercel.json` — сборка, SPA fallback, cache policy и security headers;
- `.env.production` — публичные cloud-настройки без секретов;
- Vite build `npm run build` с выходом в `dist`;
- PWA manifest и service worker;
- автоматическое обнаружение локального Hub;
- экран установки Hub и диагностики подключения;
- pairing, отдельные API/media-токены и workspace scope.

Никакие секреты для обычного deploy не требуются.

## Импорт через Vercel Dashboard

1. Открой Vercel Dashboard и выбери **Add New → Project**.
2. Импортируй репозиторий `resarytrew/PaperFlow-Stream`.
3. В **Root Directory** выбери `frontend`.
4. Проверь параметры:

```text
Framework Preset: Vite
Install Command: npm ci
Build Command: npm run build
Output Directory: dist
Node.js Version: 22.x
```

5. Нажми **Deploy**.

`frontend/vercel.json` уже содержит install/build/output settings, поэтому после
выбора Root Directory Vercel не требует ручной настройки команд.

## Environment Variables

Для стандартного Personal Hub добавлять переменные в Vercel не обязательно:
безопасные значения уже находятся в `frontend/.env.production`.

```text
VITE_PAPERFLOW_UI_MODE=cloud
VITE_PAPERFLOW_HUB_URLS=https://127.0.0.1:17841,https://localhost:17841,http://127.0.0.1:17841,http://localhost:17841
VITE_PAPERFLOW_ALLOWED_HUB_HOSTS=
VITE_PAPERFLOW_HUB_DOWNLOAD_URL=https://github.com/resarytrew/PaperFlow-Stream/releases/latest/download/PaperFlowHubSetup.exe
VITE_PAPERFLOW_HUB_RELEASES_URL=https://github.com/resarytrew/PaperFlow-Stream/releases/latest
```

Переменные `VITE_*` видны в браузере. В них запрещено добавлять API-ключи,
пароли, signing keys и любые ученические данные.

Для будущего School Hub можно переопределить
`VITE_PAPERFLOW_ALLOWED_HUB_HOSTS`, например:

```text
paperflow.school.local
```

Для LAN Hub должен использоваться доверенный HTTPS-адрес.

## Первый запуск учителем

1. Учитель открывает production-сайт на Vercel.
2. PaperFlow ищет Hub на локальных адресах.
3. Если Hub отсутствует, интерфейс показывает кнопку **Скачать PaperFlow Hub для Windows**.
4. Учитель устанавливает модуль и запускает его в системном трее.
5. Страница автоматически повторяет подключение каждые несколько секунд.
6. Hub разрешает незнакомому HTTPS Origin только публичные discovery/pairing endpoints.
7. Учитель открывает локальную страницу кода и видит точный домен, запросивший доступ.
8. После ввода кода Origin сохраняется как доверенный клиент, привязанный к токенам и workspace.
9. Только после этого становятся доступны приватные API, изображения, экспорт и WebSocket.

Generic installer больше не требует заранее прошивать конкретный Vercel-домен.
Ручной параметр `--set-web-url` остаётся доступен для управляемых школьных
развёртываний, но для обычного Personal Hub он необязателен.

## Публикация установщика

Workflow `.github/workflows/release-personal-hub.yml`:

- запускает security-тесты;
- собирает frontend и Windows Hub;
- формирует Inno Setup installer;
- создаёт стабильный файл `PaperFlowHubSetup.exe`;
- формирует SHA-256;
- публикует файлы в GitHub Release.

При ручном запуске workflow использует тег `v0.3.0-pilot`, если не указан другой.
При наличии signing secrets установщик подписывается. Без них Windows может
показать SmartScreen для неизвестного издателя — это ожидаемо для пилотной сборки.

Стабильная ссылка интерфейса всегда указывает на:

```text
https://github.com/resarytrew/PaperFlow-Stream/releases/latest/download/PaperFlowHubSetup.exe
```

## Production и Preview deployments

Для реальной работы используй стабильный Production URL. Каждый Vercel Preview
имеет отдельный Origin и должен пройти собственный pairing. Не добавляй wildcard
`*.vercel.app`: точный Origin сохраняется автоматически только после локального
подтверждения пользователем.

## Проверка после deploy

Проверь:

1. При остановленном Hub показана кнопка скачивания и понятная диагностика.
2. После запуска Hub страница переходит к безопасному pairing.
3. Локальная страница кода показывает текущий Vercel Origin.
4. Без pairing приватный `/api` возвращает отказ.
5. После pairing открываются dashboard, камера и OCR.
6. В DevTools → Network нет запросов с изображениями или ученическими JSON на домены `vercel.app`.
7. `manifest.webmanifest` загружается, а `sw.js` имеет `Cache-Control: no-cache, no-store, must-revalidate`.

Для пилота рекомендуется Chrome или Edge на Windows 10/11.

## Что нельзя разворачивать на Vercel

Не создавай Vercel Functions или rewrites, которые проксируют `/api` в облако.
На Vercel не должны размещаться:

- `backend`;
- SQLite и backup;
- папка `storage`;
- OCR worker;
- API для загрузки листов;
- диагностические ZIP;
- ключ Yandex Vision.

Текущий `vercel.json` делает только SPA fallback на статический `index.html` и
не содержит облачного API.

## Собственный домен

После пилота можно подключить, например:

```text
https://app.paperflow.ru
```

Новый домен пройдёт отдельный pairing. Старое подключение можно отозвать в
настройках PaperFlow; wildcard-доверие между доменами не используется.
