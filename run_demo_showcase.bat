@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================================
echo  Disklavier-Auto 自己実演・舞台デモショーケース (--demo)
echo  実機未接続でも仮想チェリスト／仮想ピアニストとAIの公開推敲を自動実演します
echo ============================================================================

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv が見つかりません。先に setup_environment.bat を実行してください。
    pause
    exit /b 1
)

call "%~dp0.venv\Scripts\activate.bat"
set "PYTHONPATH=%~dp0"
set "DISKLAVIER_STAGE_DEMO=1"

echo [1/3] Ollama 接続を確認しています...
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:11434/api/tags; exit 0 } catch { exit 1 }"
if errorlevel 1 (
    where ollama >nul 2>&1
    if not errorlevel 1 (
        start "" /min ollama serve
        timeout /t 2 /nobreak >nul
    )
)

echo [2/3] デモ用舞台HUD (http://localhost:8000/?demo=1) を開きます...
echo       画面上部の「Audio Monitor: OFF」をクリックすると内蔵音源で試聴できます。
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:8000/?demo=1"

echo [3/3] 自己実演デモサーバを起動中 (終了時は Ctrl+C)...
python -m src.stage.stage_server --host 127.0.0.1 --port 8000 --demo
set "SRV_ERR=%errorlevel%"

echo [CLEANUP] 全MIDIノートを安全消音しています...
python -c "from src.stage.layer5_disklavier_output import DisklavierStageOutputLayer; out = DisklavierStageOutputLayer(port_name='Disklavier', start_worker=False); out.panic(); out.close()"

if not "%SRV_ERR%"=="0" (
    echo [ERROR] デモサーバがエラーコード %SRV_ERR% で終了しました。
    pause
    exit /b %SRV_ERR%
)
exit /b 0
