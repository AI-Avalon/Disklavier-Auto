@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================================
echo  Disklavier-Auto 初回セットアップ ＆ 環境診断 (Windows 11 / RTX 3070)
echo ============================================================================

set "UV_EXE="
where uv >nul 2>&1 && for /f "delims=" %%i in ('where uv') do set "UV_EXE=%%i"
if not defined UV_EXE if exist "%LOCALAPPDATA%\hermes\tools\uv-0.13.5-win32-x64\uv.exe" (
    set "UV_EXE=%LOCALAPPDATA%\hermes\tools\uv-0.13.5-win32-x64\uv.exe"
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Python 仮想環境 (.venv) を作成しています...
    if defined UV_EXE (
        "%UV_EXE%" venv .venv --python 3.12
    ) else (
        python -m venv .venv
    )
    if errorlevel 1 (
        echo [ERROR] .venv の作成に失敗しました。Python 3.11+ または uv を確認してください。
        pause
        exit /b 1
    )
)

call "%~dp0.venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] .venv の有効化に失敗しました。
    pause
    exit /b 1
)

echo [2/4] 必須パッケージをインストールしています...
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

echo [3/4] MIDI / オーディオ / Ollama / MIDIコーパス環境を自動スキャンしています...
set "PYTHONPATH=%~dp0"
python -m src.stage.config_scanner
if errorlevel 1 (
    echo [ERROR] 環境スキャン (config_scanner) でエラーが発生しました。
    pause
    exit /b 1
)

echo [4/4] セットアップ完了。config.local.yaml を生成しました。
echo ============================================================================
pause
exit /b 0

:pip_fail
echo [ERROR] パッケージのインストールに失敗しました。ネットワーク接続または依存関係を確認してください。
pause
exit /b 1
