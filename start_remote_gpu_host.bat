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

if "%DISKLAVIER_REMOTE_PORT%"=="" set "DISKLAVIER_REMOTE_PORT=8090"

echo ============================================================================
echo   DISKLAVIER-AUTO HOME RTX 3070 REMOTE GPU INFERENCE SERVER
echo ============================================================================

echo [1/4] Checking Ollama local LLM (http://127.0.0.1:11434)...
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:11434/api/tags; exit 0 } catch { exit 1 }"
if errorlevel 1 (
    where ollama >nul 2>&1
    if not errorlevel 1 (
        echo [INFO] Starting ollama serve with Flash Attention and Q8 KV Cache...
        start "" /min ollama serve
        timeout /t 2 /nobreak >nul
    ) else (
        echo [WARN] Ollama binary not found. Running deterministic theory + NotaGen fallback.
    )
) else (
    echo [OK] Ollama server is ready.
)

echo [2/4] Checking API Bearer token (DISKLAVIER_API_TOKEN)...
if "%DISKLAVIER_API_TOKEN%"=="" (
    for /f "usebackq delims=" %%T in (`python -c "import secrets; print(secrets.token_urlsafe(24))"`) do set "DISKLAVIER_API_TOKEN=%%T"
    echo [SECURITY WARNING] DISKLAVIER_API_TOKEN was not set!
    echo [SECURITY WARNING] Ephemeral session token generated:
    echo   DISKLAVIER_API_TOKEN=!DISKLAVIER_API_TOKEN!
    if not exist "data\telemetry" mkdir "data\telemetry"
    > "data\telemetry\remote_gpu_token.txt" echo !DISKLAVIER_API_TOKEN!
) else (
    echo [OK] DISKLAVIER_API_TOKEN configured from environment.
)

echo [3/4] Detecting Tailscale IPv4 for Venue M1 MacBook Pro...
set "TS_IP="
where tailscale >nul 2>&1
if not errorlevel 1 (
    for /f "usebackq delims=" %%I in (`tailscale ip -4 2^>nul`) do (
        if "!TS_IP!"=="" set "TS_IP=%%I"
    )
)
echo   Local URL     : http://127.0.0.1:%DISKLAVIER_REMOTE_PORT%
if not "%TS_IP%"=="" (
    echo   Tailscale URL : http://%TS_IP%:%DISKLAVIER_REMOTE_PORT%
    echo   Mac Check Cmd : python3 scripts/check_remote_connection.py --url http://%TS_IP%:%DISKLAVIER_REMOTE_PORT% --token !DISKLAVIER_API_TOKEN!
    echo   Mac Start Cmd : ./start_mac_stage.sh --remote-url http://%TS_IP%:%DISKLAVIER_REMOTE_PORT% --token !DISKLAVIER_API_TOKEN! --mode AUTO --production
) else (
    echo   Tailscale IP  : Not detected ^(install/login Tailscale or run start_external_tunnel.bat^)
)
echo ============================================================================

echo [4/4] Starting watchdog loop ^(auto-restarts in 3s on crash; Ctrl+C to stop^)...
set /a RESTART_COUNT=0
:WATCHDOG_LOOP
set /a RESTART_COUNT+=1
echo [WATCHDOG #%RESTART_COUNT%] Launching remote_gpu_server on 0.0.0.0:%DISKLAVIER_REMOTE_PORT% at %DATE% %TIME%
python -m src.stage.remote_gpu_server --host 0.0.0.0 --port %DISKLAVIER_REMOTE_PORT%
set "EXIT_CODE=%errorlevel%"
echo [WATCHDOG] remote_gpu_server exited with code %EXIT_CODE%. Restarting in 3 seconds...
timeout /t 3 /nobreak >nul
goto WATCHDOG_LOOP
