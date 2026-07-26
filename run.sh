#!/usr/bin/env bash
# PaperFlow Hybrid Hub — one-command launcher (Linux / macOS).

set -euo pipefail
cd "$(dirname "$0")"

PORT="${PAPERFLOW_PORT:-${PAPERFLOW_HUB_PORT:-17841}}"
export PAPERFLOW_HUB_PORT="${PORT}"
export PAPERFLOW_HUB_PUBLIC_URL="${PAPERFLOW_HUB_PUBLIC_URL:-http://127.0.0.1:${PORT}}"
PY="${PYTHON:-python3}"

say() { printf '\033[1;34m[PaperFlow]\033[0m %s\n' "$*"; }
install_backend_deps() {
  (cd backend && .venv/bin/pip install --disable-pip-version-check -q -r requirements.txt)
}

# ---------------------------------------------------------------- python env
if [ ! -x backend/.venv/bin/python ]; then
  say "Первый запуск: создаю окружение Python (это займёт пару минут)…"
  "$PY" -m venv backend/.venv
  install_backend_deps
  say "Зависимости Python установлены."
elif ! backend/.venv/bin/python - <<'PY' >/dev/null 2>&1
import cv2
import rapidocr_onnxruntime
PY
then
  say "Обновляю зависимости Python (проверка OpenCV/OCR не прошла)…"
  install_backend_deps
  say "Зависимости Python обновлены."
fi

# ---------------------------------------------------------------- web ui
if [ ! -f frontend/dist/index.html ]; then
  if command -v npm >/dev/null 2>&1; then
    say "Собираю веб-интерфейс…"
    (cd frontend && npm install --no-audit --no-fund && npm run build)
  else
    say "ВНИМАНИЕ: Node.js не найден и frontend/dist отсутствует."
    say "Интерфейс не будет доступен. Установите Node.js LTS и запустите снова."
  fi
fi

# ---------------------------------------------------------------- run
say "Запускаю персональный Hub на http://localhost:${PORT}"
( sleep 2
  if command -v xdg-open >/dev/null 2>&1; then xdg-open "http://localhost:${PORT}" >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1; then open "http://localhost:${PORT}" || true
  fi
) &

cd backend
# Access log отключён: read-only media-token не должен попадать в URL-журнал.
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT}" --no-access-log
