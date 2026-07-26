# PaperFlow Hybrid Hub

Система потокового сканирования письменных ответов учеников:
веб-камера → автоматический захват листа → выравнивание → QR → локальный архив →
OCR → проверка учителем → экспорт.

## Архитектура 0.3

PaperFlow разделён на два контура:

- **PaperFlow Web** — браузерный/PWA-интерфейс, который можно разместить в Yandex Cloud;
- **PaperFlow Hub** — локальный FastAPI/OpenCV/OCR-сервис на компьютере учителя или в школьной сети.

ФИО, классы, изображения листов, OCR-текст, ответы и оценки обрабатываются только
внутри Hub. Веб-клиент разрешает подключение только к loopback, `.local`, RFC1918
или явно разрешённым внутренним адресам. Внешний Origin обязан пройти локальное
сопряжение по шестизначному коду; токены хешируются и привязываются к Origin и
рабочему пространству.

Подробное описание: [`docs/HYBRID_HUB_ARCHITECTURE.md`](docs/HYBRID_HUB_ARCHITECTURE.md).

## Быстрый локальный запуск

Требуется Python 3.11+; Node.js LTS нужен при первой сборке интерфейса.

**Windows:** двойной клик по `run.bat`.

**Linux / macOS:**

```bash
./run.sh
```

Персональный Hub откроется на <http://localhost:17841>. Эти скрипты предназначены
для локальной разработки и автономной работы. Для подключения облачного HTTPS-
интерфейса нужен подписанный установщик Hub с локально доверенным сертификатом.

### Ручной запуск для разработки

```bash
# backend / Hub
cd backend
python -m venv .venv
.venv/bin/pip install -r requirements.txt
PAPERFLOW_HUB_PUBLIC_URL=http://127.0.0.1:17841 \
  .venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 17841

# frontend — same-origin dev proxy
cd frontend
npm install
npm run dev
```

## Настройка внешнего web-интерфейса

На Hub необходимо указать точный Origin размещённого интерфейса:

```bash
PAPERFLOW_HUB_ALLOWED_ORIGINS='["https://paperflow.example.ru"]'
PAPERFLOW_HUB_PUBLIC_URL=https://127.0.0.1:17841
```

Для cloud-сборки frontend:

```bash
VITE_PAPERFLOW_UI_MODE=cloud
VITE_PAPERFLOW_HUB_URLS=https://127.0.0.1:17841,https://localhost:17841
npm run build
```

Все production-подключения cloud → Hub должны использовать локально доверенный
HTTPS. Открытый HTTP допустим только для локальной разработки.

## Подготовка к School Hub

API уже использует стабильный контракт рабочего пространства
`X-PaperFlow-Workspace` и request context с `workspace_id`, `actor_id`, ролью и
идентификатором клиента. Переход в school mode намеренно заблокирован, пока не
установлена tenant-scoped миграция базы и управление пользователями:

```text
PAPERFLOW_HUB_MODE=school
PAPERFLOW_HUB_SCHOOL_TENANCY_ENABLED=true
```

Флаг нельзя включать в текущем релизе вручную: он предназначен для следующей
версии схемы, где каждая бизнес-сущность будет привязана к workspace.

## Типовой сценарий

1. Создать класс, учеников и задание.
2. Сформировать PDF-бланки с QR.
3. Откалибровать камеру.
4. Запустить сессию и подавать листы.
5. Проверить OCR и исправить ответы.
6. Экспортировать CSV, JSON, XLSX или ZIP.
7. Создать локальную резервную копию через `/api/maintenance/backup`.

## Тесты

```bash
cd backend && .venv/bin/python -m pytest
cd frontend && npm run build
```

GitHub Actions запускает оба набора проверок при каждом push в `main` и в pull request.

## Ограничения

- RapidOCR ориентирован преимущественно на печатный текст; русский рукописный
  текст часто требует проверки учителем.
- Облачный Yandex Vision OCR остаётся отдельной opt-in функцией и означает
  передачу выбранного изображения провайдеру. Для режима «нулевая передача» его
  следует держать отключённым.
- Production-установщик с системным треем, автозапуском и локально доверенным TLS-
  сертификатом является следующим этапом; протокол и API для него уже заложены.
