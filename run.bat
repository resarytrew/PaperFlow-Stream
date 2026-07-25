@echo off
rem PaperFlow Stream - one-command launcher (Windows).
rem Creates the Python environment on first run, builds the web UI if
rem Node.js is available, then starts a single local server and opens
rem the browser. Everything stays on this computer.

setlocal
cd /d "%~dp0"
if "%PAPERFLOW_PORT%"=="" set PAPERFLOW_PORT=8000

if not exist backend\.venv\Scripts\python.exe (
    echo [PaperFlow] Pervyj zapusk: sozdaju okruzhenie Python...
    python -m venv backend\.venv || goto :error
    backend\.venv\Scripts\pip install --disable-pip-version-check -q -r backend\requirements.txt || goto :error
)

if not exist frontend\dist\index.html (
    where npm >nul 2>nul
    if %errorlevel%==0 (
        echo [PaperFlow] Sobiraju veb-interfejs...
        pushd frontend
        call npm install --no-audit --no-fund || (popd & goto :error)
        call npm run build || (popd & goto :error)
        popd
    ) else (
        echo [PaperFlow] VNIMANIE: Node.js ne najden i frontend\dist otsutstvuet.
        echo [PaperFlow] Ustanovite Node.js LTS i zapustite snova.
    )
)

echo [PaperFlow] Server: http://localhost:%PAPERFLOW_PORT%
start "" /b cmd /c "timeout /t 2 >nul & start http://localhost:%PAPERFLOW_PORT%"

cd backend
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port %PAPERFLOW_PORT%
goto :eof

:error
echo [PaperFlow] Oshibka zapuska. Prover'te, chto ustanovlen Python 3.11+.
pause
