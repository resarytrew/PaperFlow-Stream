#!/usr/bin/env bash
# PaperFlow Stream — one-command launcher (Linux / macOS).
#
# Creates the Python environment on first run, builds the web UI if Node.js
# is available, then starts a single local server and opens the browser.
# Everything stays on this computer.

set -euo pipefail
cd "$(dirname "$0")"

PORT="${PAPERFLOW_PORT:-8000}"
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
say "Запускаю сервер на http://localhost:${PORT}"
( sleep 2
  if command -v xdg-open >/dev/null 2>&1; then xdg-open "http://localhost:${PORT}" >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1; then open "http://localhost:${PORT}" || true
  fi
) &

cd backend
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT}"
