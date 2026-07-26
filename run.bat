@echo off
rem PaperFlow Hybrid Hub - one-command development launcher for Windows.
rem Production cloud pairing requires the signed installer and trusted HTTPS.

setlocal
cd /d "%~dp0"
if "%PAPERFLOW_PORT%"=="" set PAPERFLOW_PORT=17841
set PAPERFLOW_HUB_PORT=%PAPERFLOW_PORT%
if "%PAPERFLOW_HUB_PUBLIC_URL%"=="" set PAPERFLOW_HUB_PUBLIC_URL=http://127.0.0.1:%PAPERFLOW_PORT%

if not exist backend\.venv\Scripts\python.exe (
    echo [PaperFlow] Pervyj zapusk: sozdaju okruzhenie Python...
    python -m venv backend\.venv || goto :error
    call :install_backend_deps || goto :error
) else (
    backend\.venv\Scripts\python -c "import cv2, rapidocr_onnxruntime" >nul 2>nul
    if errorlevel 1 (
        echo [PaperFlow] Obnovljaju zavisimosti Python ^(proverka OpenCV/OCR ne proshla^)...
        call :install_backend_deps || goto :error
    )
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

echo [PaperFlow] Personal Hub: http://localhost:%PAPERFLOW_PORT%
start "" /b cmd /c "timeout /t 2 >nul & start http://localhost:%PAPERFLOW_PORT%"

cd backend
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port %PAPERFLOW_PORT%
goto :eof

:install_backend_deps
pushd backend
.venv\Scripts\pip install --disable-pip-version-check -q -r requirements.txt
set INSTALL_STATUS=%errorlevel%
popd
exit /b %INSTALL_STATUS%

:error
echo [PaperFlow] Oshibka zapuska. Prover'te, chto ustanovlen Python 3.11+.
pause
