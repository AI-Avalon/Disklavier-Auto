@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
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
echo [1/4] Running stage system verification tests (tests/test_stage_system.py)...
pytest -v tests/test_stage_system.py
if errorlevel 1 (
    echo [FAIL] tests/test_stage_system.py failed.
    set "FAIL_FLAG=1"
) else (
    echo [PASS] tests/test_stage_system.py passed.
)

echo.
echo [2/4] Running full test suite (pytest tests/)...
pytest -v tests/
if errorlevel 1 (
    echo [FAIL] pytest tests/ failed.
    set "FAIL_FLAG=1"
) else (
    echo [PASS] pytest tests/ passed.
)

echo.
echo [3/4] Running system integrity validation (validate_system_integrity.py)...
python validate_system_integrity.py
if errorlevel 1 (
    echo [FAIL] validate_system_integrity.py failed.
    set "FAIL_FLAG=1"
) else (
    echo [PASS] validate_system_integrity.py passed.
)

echo.
echo [4/4] Running 10x real LLM + MusicalityCritic + Dual Pedal + Audio loop...
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
