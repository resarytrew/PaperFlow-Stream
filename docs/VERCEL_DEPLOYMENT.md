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

Итоговые публичные параметры сборки:

```text
VITE_PAPERFLOW_UI_MODE=cloud
VITE_PAPERFLOW_HUB_URLS=https://127.0.0.1:17841,https://localhost:17841,http://127.0.0.1:17841,http://localhost:17841
VITE_PAPERFLOW_ALLOWED_HUB_HOSTS=
```

Переменные `VITE_*` видны в браузере. В них запрещено добавлять:

- API-ключ Yandex Vision;
- service-account credentials;
- пароли и signing keys;
- ФИО, классы, ответы, OCR или другие ученические данные.

Для будущего School Hub можно переопределить
`VITE_PAPERFLOW_ALLOWED_HUB_HOSTS`, например:

```text
paperflow.school.local
```

Для LAN Hub должен использоваться доверенный HTTPS-адрес.

## Привязка Vercel-домена к Personal Hub

После первого production deploy Vercel выдаст постоянный адрес, например:

```text
https://paperflow-stream.vercel.app
```

Этот точный Origin нужно сохранить в установленном Hub:

```powershell
PaperFlowHub.exe --set-web-url "https://paperflow-stream.vercel.app"
```

После изменения перезапусти PaperFlow Hub. При новой установке адрес можно
передать установщику:

```text
PaperFlowHubSetup-0.3.0.exe /WebUrl=https://paperflow-stream.vercel.app
```

Hub разрешает только точный Origin. Это намеренно защищает локальные данные от
посторонних сайтов.

## Production и Preview deployments

Для реальной работы используй стабильный Production URL. Каждый Vercel Preview
имеет отдельный Origin, поэтому он не получает доступ к Hub автоматически.

Не добавляй wildcard `*.vercel.app` в разрешённые Origins. Для тестирования
конкретного Preview временно добавляй только его полный HTTPS Origin, а затем
удаляй подключение через раздел настроек PaperFlow.

## Пользовательский сценарий

1. Учитель устанавливает PaperFlow Hub один раз.
2. Hub запускается в фоне и слушает `127.0.0.1:17841`.
3. Учитель открывает production-сайт на Vercel.
4. Браузер запрашивает разрешение на доступ к локальной сети/loopback.
5. PaperFlow показывает локальный шестизначный код pairing.
6. После подтверждения интерфейс работает с локальным Hub.
7. Ученические данные не отправляются в Vercel.

Для пилота рекомендуется Chrome или Edge на Windows 10/11.

## Проверка после deploy

Проверь:

1. Главная страница открывается без 404.
2. Обновление страницы и переход по hash-маршрутам работают.
3. `manifest.webmanifest` загружается.
4. `sw.js` имеет `Cache-Control: no-cache, no-store, must-revalidate`.
5. В DevTools → Network нет запросов с изображениями или ученическими JSON на
   домены `vercel.app`.
6. Запросы приложения идут напрямую к `127.0.0.1:17841` или другому разрешённому
   локальному Hub.
7. При остановленном Hub интерфейс показывает экран подключения, а не пытается
   использовать облачный backend.

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

После смены домена обязательно обнови Web URL в Hub и выполни pairing заново.
Старое подключение можно отозвать в настройках PaperFlow.
