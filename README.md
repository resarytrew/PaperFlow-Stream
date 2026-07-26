# Чистовик

Система потокового сканирования письменных ответов учеников:
веб-камера → автоматический захват листа → выравнивание → QR → локальный архив →
OCR → проверка учителем → экспорт.

## Архитектура 0.3

Чистовик разделён на два контура:

- **Веб-приложение «Чистовик»** — браузерный/PWA-интерфейс, который можно разместить в Vercel или Yandex Cloud;
- **Локальный модуль «Чистовик»** — FastAPI/OpenCV/OCR-сервис на компьютере учителя или в школьной сети.

ФИО, классы, изображения листов, OCR-текст, ответы и оценки обрабатываются только
внутри локального модуля. Веб-клиент разрешает подключение только к loopback, `.local`, RFC1918
или явно разрешённым внутренним адресам. Внешний Origin обязан пройти локальное
сопряжение по шестизначному коду; API- и media-токены разделены, хешируются и
привязываются к браузерному клиенту и рабочему пространству.

Документация:

- [`docs/HYBRID_HUB_ARCHITECTURE.md`](docs/HYBRID_HUB_ARCHITECTURE.md) — архитектура и фундамент School Hub;
- [`docs/VERCEL_DEPLOYMENT.md`](docs/VERCEL_DEPLOYMENT.md) — пилотный deploy Чистовик на Vercel;
- [`deploy/yandex-cloud/README.md`](deploy/yandex-cloud/README.md) — статический deploy в Yandex Cloud.

## Быстрый локальный запуск

Требуется Python 3.11+; Node.js LTS нужен при первой сборке интерфейса.

**Windows:** двойной клик по `run.bat`.

**Linux / macOS:**

```bash
./run.sh
```

Локальный модуль «Чистовик» откроется на <http://localhost:17841>. Эти скрипты предназначены
для разработки и автономной работы.

### Ручной запуск

```bash
# backend / локальный модуль
cd backend
python -m venv .venv
.venv/bin/pip install -r requirements.txt
PAPERFLOW_HUB_PUBLIC_URL=http://127.0.0.1:17841 \
  .venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 17841 --no-access-log

# frontend — same-origin dev proxy
cd frontend
npm install
npm run dev
```

## Локальный модуль для Windows

Добавлен desktop-host `app.desktop`:

- запускает локальный модуль без консоли;
- работает в системном трее;
- открывает Чистовик;
- создаёт backup из меню;
- открывает локальную папку данных;
- устанавливает автозапуск текущего пользователя;
- хранит данные вне каталога программы, поэтому удаление приложения не удаляет архив.

Релизный workflow `.github/workflows/release-personal-hub.yml` собирает:

```text
ChistovikSetup-0.3.2.exe
ChistovikSetup-0.3.2.exe.sha256
```

Он запускает security-тесты, собирает frontend, PyInstaller-пакет и per-user
Inno Setup installer. При наличии GitHub Secrets `WINDOWS_CERTIFICATE_BASE64` и
`WINDOWS_CERTIFICATE_PASSWORD` исполняемые файлы подписываются автоматически.

Установщик можно собрать вручную на Windows:

```powershell
cd frontend
npm ci
npm run build
cd ..\backend
pip install -r requirements-desktop.txt
cd ..
python -m PyInstaller packaging\windows\PaperFlowHub.spec --noconfirm --clean
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\windows\PaperFlowHub.iss
```

При установке можно передать адрес облачного интерфейса:

```text
ChistovikSetup-0.3.2.exe /WebUrl=https://chistovik.example.ru
```

## Настройка внешнего web-интерфейса

Desktop-host автоматически разрешает точный Origin из сохранённого `web_url`.
При ручном запуске Origin можно задать явно:

```bash
PAPERFLOW_HUB_ALLOWED_ORIGINS='["https://chistovik.example.ru"]'
PAPERFLOW_HUB_PUBLIC_URL=http://127.0.0.1:17841
```

Cloud-сборка frontend:

```bash
VITE_PAPERFLOW_UI_MODE=cloud
VITE_PAPERFLOW_HUB_URLS=https://127.0.0.1:17841,https://localhost:17841,http://127.0.0.1:17841,http://localhost:17841
npm run build
```

Современный браузер может запросить у учителя разрешение на доступ к loopback-
сети и подключиться к локальному HTTP-модулю. Локально доверенный HTTPS остаётся
рекомендуемым вариантом для максимальной совместимости и для школьного модуля в локальной сети.

### Пилотный deploy на Vercel

Репозиторий подготовлен для статического Vercel-проекта:

```text
Root Directory: frontend
Framework: Vite
Install: npm ci
Build: npm run build
Output: dist
Node.js: 22.x
```

`frontend/vercel.json` содержит SPA fallback, cache policy и security headers, а
`frontend/.env.production` — безопасные публичные настройки поиска локального Hub.
Backend и ученические данные на Vercel не разворачиваются.

После deploy сохрани точный production Origin в локальном модуле:

```powershell
Chistovik.exe --set-web-url "https://paperflow-stream.vercel.app"
```

Полная инструкция: [`docs/VERCEL_DEPLOYMENT.md`](docs/VERCEL_DEPLOYMENT.md).

## Подготовка к школьной установке

API уже использует стабильный контракт `X-PaperFlow-Workspace` и request context
с `workspace_id`, `actor_id`, ролью и идентификатором клиента. В документации
зафиксированы будущие `organizations`, `workspaces`, пользователи, memberships,
RBAC, audit log и порядок tenant-миграции.

Школьный режим в 0.3 заблокирован **на уровне кода**, даже если выставить переменные
окружения. Разблокировать его сможет только релиз, в котором реально появятся
workspace-scoped таблицы, обязательные tenant predicates и управление доступом.

## Типовой сценарий

1. Создать класс, учеников и задание.
2. Сформировать PDF-бланки с QR.
3. Откалибровать камеру.
4. Запустить сессию и подавать листы.
5. Проверить OCR и исправить ответы.
6. Экспортировать CSV, JSON, XLSX или ZIP.
7. Создать локальную резервную копию в настройках.

## Тесты

```bash
cd backend && .venv/bin/python -m pytest
cd frontend && npm run build
```

GitHub Actions запускает backend и frontend проверки при push в `main` и pull
request. Отдельный workflow собирает Windows installer вручную или по тегу `v*`.

## Ограничения

- RapidOCR ориентирован преимущественно на печатный текст; русский рукописный
  текст часто требует проверки учителем.
- Облачный Yandex Vision OCR остаётся отдельной opt-in функцией и означает
  передачу выбранного изображения провайдеру. Для режима «нулевая передача» его
  следует держать отключённым.
- Репозиторий содержит сборочный pipeline установщика, но готовый подписанный
  релиз появляется только после успешного запуска release workflow и настройки
  сертификата подписи.
- Production URL Vercel или другого web-хостинга должен быть явно разрешён в
  Чистовик; wildcard Origins запрещены архитектурой.
- Развёртывание статического frontend в конкретном аккаунте Yandex Cloud требует
  bucket/domain/CDN credentials владельца инфраструктуры.
