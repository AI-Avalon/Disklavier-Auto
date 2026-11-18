@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

echo ============================================================================
echo  Disklavier-Auto Environment Setup (Windows 11 / RTX 3070)
echo ============================================================================

set "UV_EXE="
where uv >nul 2>&1 && for /f "delims=" %%i in ('where uv') do set "UV_EXE=%%i"
if not defined UV_EXE if exist "%LOCALAPPDATA%\hermes\tools\uv-0.13.5-win32-x64\uv.exe" (
    set "UV_EXE=%LOCALAPPDATA%\hermes\tools\uv-0.13.5-win32-x64\uv.exe"
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Creating Python virtual environment .venv ...
    if defined UV_EXE (
        "%UV_EXE%" venv .venv --python 3.12
    ) else (
        python -m venv .venv
    )
    if errorlevel 1 (
        echo [ERROR] Failed to create .venv. Check Python 3.11+ or uv.
        pause
        exit /b 1
    )
)

call "%~dp0.venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] Failed to activate .venv.
    pause
    exit /b 1
)

set "PYTHONPATH=%~dp0"
python -m src.stage.cli_banner setup_header
python -m src.stage.cli_banner setup_step2

if defined UV_EXE (
    "%UV_EXE%" pip install --python "%~dp0.venv\Scripts\python.exe" fastapi uvicorn websockets pydantic mido python-rtmidi sounddevice soundfile numpy scipy matplotlib requests symusic miditok ollama pyyaml httpx pytest ruff
    if errorlevel 1 goto :pip_fail
    "%UV_EXE%" pip install --python "%~dp0.venv\Scripts\python.exe" -e ".[audio,dev]" --no-deps
    if errorlevel 1 goto :pip_fail
) else (
    python -m pip install --upgrade pip
    python -m pip install fastapi uvicorn websockets pydantic mido python-rtmidi sounddevice soundfile numpy scipy matplotlib requests symusic miditok ollama pyyaml httpx pytest ruff
    if errorlevel 1 goto :pip_fail
    python -m pip install -e ".[audio,dev]" --no-deps
    if errorlevel 1 goto :pip_fail
)

python -m src.stage.cli_banner setup_step3
python -m src.stage.config_scanner
if errorlevel 1 (
    echo [ERROR] Environment scan failed in src.stage.config_scanner.
    pause
    exit /b 1
)

python -m src.stage.cli_banner setup_step4
pause
exit /b 0

:pip_fail
echo [ERROR] Package installation failed. Check network connection or dependencies.
pause
exit /b 1
