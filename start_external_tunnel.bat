@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "PYTHONUTF8=1"

if "%DISKLAVIER_REMOTE_PORT%"=="" set "DISKLAVIER_REMOTE_PORT=8090"

echo ============================================================================
echo   DISKLAVIER-AUTO EXTERNAL TUNNEL LAUNCHER ^(Tailscale + Cloudflare Tunnel^)
echo ============================================================================

set "TS_IP="
where tailscale >nul 2>&1
if not errorlevel 1 (
    for /f "usebackq delims=" %%I in (`tailscale ip -4 2^>nul`) do (
        if "!TS_IP!"=="" set "TS_IP=%%I"
    )
)
if not "%TS_IP%"=="" (
    echo [TAILSCALE] Active IPv4: http://%TS_IP%:%DISKLAVIER_REMOTE_PORT%
) else (
    echo [TAILSCALE] IPv4 not active or Tailscale not installed.
)

where cloudflared >nul 2>&1
if errorlevel 1 (
    echo [ERROR] cloudflared.exe not found in PATH.
    echo         Install Cloudflare Tunnel: winget install Cloudflare.cloudflared
    pause
    exit /b 1
)

if exist "data\telemetry\remote_gpu_token.txt" (
    set /p SAVED_TOK=<"data\telemetry\remote_gpu_token.txt"
    echo [TOKEN] Current token from remote_gpu_token.txt: !SAVED_TOK!
) else if not "%DISKLAVIER_API_TOKEN%"=="" (
    echo [TOKEN] Using DISKLAVIER_API_TOKEN from environment: %DISKLAVIER_API_TOKEN%
) else (
    echo [WARN] Run start_remote_gpu_host.bat first so API server & token are active.
)

echo [INFO] Target local GPU server: http://127.0.0.1:%DISKLAVIER_REMOTE_PORT%
echo [INFO] Watchdog enabled ^(restarts tunnel in 3s if disconnected; Ctrl+C to stop^).
echo ============================================================================

:TUNNEL_LOOP
if not "%CLOUDFLARE_TUNNEL_TOKEN%"=="" (
    echo [TUNNEL] Starting named Cloudflare Tunnel...
    cloudflared tunnel run --token %CLOUDFLARE_TUNNEL_TOKEN%
) else (
    echo [TUNNEL] Starting Cloudflare Quick Tunnel ^(copy the https://*.trycloudflare.com URL below^)...
    cloudflared tunnel --url http://127.0.0.1:%DISKLAVIER_REMOTE_PORT%
)
echo [TUNNEL] Tunnel exited with code %errorlevel%. Restarting in 3 seconds...
timeout /t 3 /nobreak >nul
goto TUNNEL_LOOP
