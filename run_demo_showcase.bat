@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "OLLAMA_FLASH_ATTENTION=1"
set "OLLAMA_KV_CACHE_TYPE=q8_0"
set "OLLAMA_KEEP_ALIVE=-1"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run setup_environment.bat first.
    pause
    exit /b 1
)

call "%~dp0.venv\Scripts\activate.bat"
set "PYTHONPATH=%~dp0"
set "DISKLAVIER_STAGE_DEMO=1"
set "DISKLAVIER_MONITOR_AUDIO=1"
set "DISKLAVIER_STAGE_PRODUCTION=0"

python -m src.stage.cli_banner demo_header

echo [1/3] Checking Ollama local LLM server (http://127.0.0.1:11434)...
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:11434/api/tags; exit 0 } catch { exit 1 }"
if errorlevel 1 (
    where ollama >nul 2>&1
    if not errorlevel 1 (
        echo [INFO] Starting Ollama serve with Flash Attention and Q8 KV Cache...
        start "" /min ollama serve
        timeout /t 2 /nobreak >nul
    )
) else (
    echo [OK] Ollama server connected.
)

python -m src.stage.cli_banner demo_open
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:8000/studio?demo=1"

python -m src.stage.cli_banner demo_run
python -m src.stage.stage_server --host 127.0.0.1 --port 8000 --demo --monitor-audio
set "SRV_ERR=%errorlevel%"

echo [CLEANUP] Sending All Notes Off / Panic...
python -c "from src.stage.layer5_disklavier_output import DisklavierStageOutputLayer; out = DisklavierStageOutputLayer(port_name='Disklavier', start_worker=False); out.panic(); out.close()"

if not "%SRV_ERR%"=="0" (
    echo [ERROR] Demo server exited with error code %SRV_ERR%.
    pause
    exit /b %SRV_ERR%
)
exit /b 0
