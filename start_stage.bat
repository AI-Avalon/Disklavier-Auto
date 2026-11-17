@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================================
echo  Disklavier-Auto 本番・リハーサル起動ランチャー (http://localhost:8000)
echo ============================================================================

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv が見つかりません。先に setup_environment.bat を実行してください。
    pause
    exit /b 1
)

call "%~dp0.venv\Scripts\activate.bat"
set "PYTHONPATH=%~dp0"

echo [1/3] Ollama ローカルLLMサーバを確認しています...
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:11434/api/tags; exit 0 } catch { exit 1 }"
if errorlevel 1 (
    where ollama >nul 2>&1
    if not errorlevel 1 (
        echo Ollama をバックグラウンドで起動します...
        start "" /min ollama serve
        timeout /t 2 /nobreak >nul
    ) else (
        echo [INFO] Ollama が見つからないため、ルールベース室内楽フォールバックで動作します。
    )
) else (
    echo Ollama 接続OK。
)

echo [2/3] 舞台投影Web HUD (http://localhost:8000) をブラウザで開きます...
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:8000"

echo [3/3] 舞台サーバを起動しています (終了時は Ctrl+C)...
python -m src.stage.stage_server --host 127.0.0.1 --port 8000
set "SRV_ERR=%errorlevel%"

echo [CLEANUP] 全MIDIノートを安全消音 (All Notes Off) しています...
python -c "from src.stage.layer5_disklavier_output import DisklavierStageOutputLayer; out = DisklavierStageOutputLayer(port_name='Disklavier', start_worker=False); out.panic(); out.close()"

if not "%SRV_ERR%"=="0" (
    echo [ERROR] サーバがエラーコード %SRV_ERR% で終了しました。
    pause
    exit /b %SRV_ERR%
)
exit /b 0
