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
set "FAIL_FLAG=0"

python -m src.stage.cli_banner check_header

echo.
echo [1/6] Running stage system verification tests (tests/test_stage_system.py)...
pytest -v tests/test_stage_system.py
if errorlevel 1 (
    echo [FAIL] tests/test_stage_system.py failed.
    set "FAIL_FLAG=1"
) else (
    echo [PASS] tests/test_stage_system.py passed.
)

echo.
echo [2/6] Running full test suite (pytest tests/)...
pytest -v tests/
if errorlevel 1 (
    echo [FAIL] pytest tests/ failed.
    set "FAIL_FLAG=1"
) else (
    echo [PASS] pytest tests/ passed.
)

echo.
echo [3/6] Running system integrity validation (validate_system_integrity.py)...
python validate_system_integrity.py
if errorlevel 1 (
    echo [FAIL] validate_system_integrity.py failed.
    set "FAIL_FLAG=1"
) else (
    echo [PASS] validate_system_integrity.py passed.
)

echo.
echo [4/6] Verifying 100-piece library and 10 showcase MP3/MIDI assets...
python scripts/verify_catalog_and_demo.py
if errorlevel 1 (
    echo [FAIL] scripts/verify_catalog_and_demo.py failed.
    set "FAIL_FLAG=1"
) else (
    echo [PASS] scripts/verify_catalog_and_demo.py passed.
)

echo.
echo [5/6] Running CI simulation and Playwright browser audit (/studio, /stage, web-demo)...
python scripts/simulate_ci_local.py
if errorlevel 1 (
  echo [FAIL] CI local simulation failed!
  if "%CI_NO_PAUSE%"=="" pause
  exit /b 1
)
python scripts/playwright_audit.py --quick
if errorlevel 1 (
    echo [FAIL] scripts/playwright_audit.py failed.
    set "FAIL_FLAG=1"
) else (
    echo [PASS] scripts/playwright_audit.py passed.
)

echo.
echo [6/6] Running 10x real LLM + MusicalityCritic + Dual Pedal + Audio loop...
python scripts/run_10x_iterative_refinement.py --iterations 10 --no-device-audio
if errorlevel 1 (
    echo [FAIL] scripts/run_10x_iterative_refinement.py failed.
    set "FAIL_FLAG=1"
) else (
    echo [PASS] 10x iterative refinement passed.
)

echo.
echo ============================================================================
if "%FAIL_FLAG%"=="0" (
    python -m src.stage.cli_banner check_pass
) else (
    python -m src.stage.cli_banner check_fail
)
echo ============================================================================
if not defined CI_NO_PAUSE pause
exit /b %FAIL_FLAG%
